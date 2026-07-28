import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { FC, PropsWithChildren } from "react";
import { LOGIN_PATH } from "@/lib/chat-route";

export type AuthUser = {
  id: string;
  email: string;
  display_name: string | null;
  role: "admin" | "member";
  /** Groupes effectifs, `public` compris — ils décident de ce que le RAG rend. */
  groups: string[];
};

export type AuthState =
  | { state: "loading" }
  | { state: "anonymous" }
  | { state: "authenticated"; user: AuthUser };

type AuthContextValue = AuthState & {
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const urlOf = (input: RequestInfo | URL): string =>
  typeof input === "string"
    ? input
    : input instanceof URL
      ? input.href
      : input.url;

const isApiRequest = (input: RequestInfo | URL): boolean =>
  urlOf(input).includes("/api/");

/**
 * Les endpoints d'auth ne doivent JAMAIS déclencher de refresh : intercepter un
 * 401 de `/api/auth/refresh` boucrerait à l'infini, et un 401 de `login` est une
 * information légitime (identifiants faux), pas une session à renouveler.
 */
const isAuthEndpoint = (input: RequestInfo | URL): boolean => {
  const url = urlOf(input);
  return (
    url.includes("/api/auth/login") ||
    url.includes("/api/auth/logout") ||
    url.includes("/api/auth/refresh")
  );
};

/**
 * Ramène l'URL sur `/login` sans recharger la page. Appelé **avant** de passer
 * `anonymous` : `<LoginScreen/>` lit l'URL à son montage (l'autofill de démo en
 * dépend), donc elle doit déjà valoir `/login` à ce moment-là — un changement
 * d'URL fait après coup, dans un effet, arriverait trop tard.
 */
const goToLogin = (): void => {
  if (window.location.pathname !== LOGIN_PATH) {
    window.history.replaceState(null, "", LOGIN_PATH);
  }
};

/**
 * Session courante, et son cycle de vie.
 *
 * Le jeton n'est **jamais** manipulé ici : il vit dans un cookie `httpOnly` que
 * le navigateur envoie tout seul. Ce composant ne connaît donc que l'identité,
 * jamais le secret — c'est ce qui rend une XSS incapable de voler la session.
 */
export const AuthProvider: FC<PropsWithChildren> = ({ children }) => {
  const [auth, setAuth] = useState<AuthState>({ state: "loading" });

  // Le jeton d'accès est court (15 min) : il expire donc en cours de session,
  // pendant que l'utilisateur lit ou discute. À chaque 401 d'API, on tente UN
  // refresh silencieux, puis on rejoue la requête — y compris celles émises par le
  // transport interne d'assistant-ui, que ce code n'appelle pas lui-même et ne
  // peut donc instrumenter qu'ici, en enveloppant `fetch`.
  useEffect(() => {
    const original = window.fetch;

    // Un seul refresh en vol : assistant-ui émet plusieurs requêtes en parallèle,
    // et chacune tombant en 401 déclencherait sinon son propre refresh — donc une
    // rotation concurrente, que la détection de rejeu prendrait pour un vol. Toutes
    // partagent la même promesse.
    let refreshing: Promise<boolean> | null = null;

    const refreshOnce = (): Promise<boolean> => {
      if (!refreshing) {
        refreshing = original("/api/auth/refresh", { method: "POST" })
          .then((response) => response.ok)
          .catch(() => false)
          .finally(() => {
            refreshing = null;
          });
      }
      return refreshing;
    };

    const toAnonymous = () => {
      goToLogin();
      setAuth((current) =>
        current.state === "authenticated" ? { state: "anonymous" } : current,
      );
    };

    window.fetch = async (...args: Parameters<typeof fetch>) => {
      const response = await original(...args);

      if (
        response.status !== 401 ||
        !isApiRequest(args[0]) ||
        isAuthEndpoint(args[0])
      ) {
        return response;
      }

      const refreshed = await refreshOnce();
      if (!refreshed) {
        toAnonymous();
        return response;
      }

      // Rejeu unique avec le cookie d'accès renouvelé. `args` porte un corps en
      // chaîne (JSON) côté assistant-ui et nos appels : il est donc rejouable. Le
      // rejeu passe par `original`, jamais par ce wrapper — pas de récursion.
      const replay = await original(...args);
      if (replay.status === 401) toAnonymous();
      return replay;
    };

    return () => {
      window.fetch = original;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    fetch("/api/auth/me", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          goToLogin();
          return setAuth({ state: "anonymous" });
        }
        const body = (await response.json()) as { user: AuthUser };
        setAuth({ state: "authenticated", user: body.user });
      })
      .catch(() => {
        // Backend injoignable : on affiche l'écran de connexion, qui rendra
        // l'erreur réelle à la première tentative plutôt que de bloquer ici.
        if (!controller.signal.aborted) {
          goToLogin();
          setAuth({ state: "anonymous" });
        }
      });

    return () => controller.abort();
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    if (!response.ok) {
      // Le message vient du serveur : lui seul sait distinguer « identifiants
      // incorrects » (401) de « trop de tentatives » (429), et il le formule
      // déjà sans révéler si le compte existe.
      const detail = await response
        .json()
        .then((body: { detail?: string }) => body.detail)
        .catch(() => undefined);
      throw new Error(detail ?? "Connexion impossible");
    }

    const body = (await response.json()) as { user: AuthUser };
    setAuth({ state: "authenticated", user: body.user });
  }, []);

  const logout = useCallback(async () => {
    // Déconnexion INSTANTANÉE, sans rechargement de page. Tout l'état de compte
    // (runtime de chat, liste des conversations, réglages) est porté par le
    // sous-arbre <ChatApp/> ; passer en `anonymous` le démonte, ce qui détruit cet
    // état d'un coup. Ne subsistent que des préférences d'UI non sensibles (thème,
    // dernier modèle) en localStorage. Un rechargement dur — l'ancienne approche —
    // garantissait la même chose mais au prix d'une SPA rechargée entièrement,
    // donc d'une déconnexion lente et d'un écran de login qui « flashait ».
    //
    // On ramène l'URL sur /login (écran de connexion canonique, autofill démo)
    // sans recharger : sinon elle resterait sur /ichat/c/:id, l'id d'une
    // conversation de la session qu'on vient de quitter.
    goToLogin();
    setAuth({ state: "anonymous" });
    // Révocation côté serveur, en arrière-plan : le cookie de refresh part avec la
    // requête (dispatchée avant tout re-login), la session est coupée. On n'attend
    // pas la réponse pour rendre la main — l'UI a déjà basculé.
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ ...auth, login, logout }),
    [auth, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth doit être utilisé dans <AuthProvider>");
  return value;
}
