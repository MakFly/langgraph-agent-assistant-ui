import { useId, useState } from "react";
import type { FC, FormEvent } from "react";
import { LoaderCircleIcon, LockIcon, ShieldCheckIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/components/auth/auth-context";
import {
  DemoPanel,
  demoCredentials,
  type DemoCredentials,
} from "@/components/auth/demo-accounts";

/**
 * Écran de connexion.
 *
 * Structure responsive : une seule colonne jusqu'à `lg`, puis deux — le panneau
 * de gauche n'apparaît qu'à partir de 1024 px, où il y a la place pour du
 * contexte sans repousser le formulaire hors de l'écran. Le formulaire, lui,
 * ne change jamais de forme : c'est le contenu autour qui s'ajoute.
 *
 * `min-h-dvh` et non `min-h-screen` : sur mobile, `100vh` inclut la barre
 * d'URL rétractable, donc le contenu se retrouve coupé au chargement.
 */
export const LoginScreen: FC = () => {
  const { login } = useAuth();
  const emailId = useId();
  const passwordId = useId();
  const errorId = useId();

  // `import.meta.env.DEV` vaut littéralement `false` au build de production :
  // l'expression entière se replie sur `null`, `demoCredentials` devient
  // inutilisé, et le module qui porte le mot de passe sort du bundle. Cette
  // forme n'est pas cosmétique — une version passant par un `useState` laissait
  // le mot de passe dans `dist/`.
  const [prefill] = useState<DemoCredentials | null>(() =>
    import.meta.env.DEV ? demoCredentials() : null,
  );

  const [email, setEmail] = useState(prefill?.email ?? "");
  const [password, setPassword] = useState(prefill?.password ?? "");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  /** Chemin unique de connexion : le formulaire et les boutons y passent tous deux. */
  const submit = async (nextEmail: string, nextPassword: string) => {
    if (pending) return;

    setPending(true);
    setError(null);
    try {
      await login(nextEmail.trim(), nextPassword);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Connexion impossible",
      );
      setPassword("");
    } finally {
      setPending(false);
    }
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submit(email, password);
  };

  /** Remplit les champs ET connecte : c'est un raccourci de démonstration. */
  const loginAs = (credentials: DemoCredentials) => {
    setEmail(credentials.email);
    setPassword(credentials.password);
    // Les valeurs sont passées explicitement : `setState` n'est pas synchrone,
    // lire `email` juste après l'aurait laissé à sa valeur précédente.
    void submit(credentials.email, credentials.password);
  };

  return (
    <div className="bg-background text-foreground min-h-dvh">
      <div className="mx-auto grid min-h-dvh w-full max-w-[1440px] lg:grid-cols-[1.1fr_1fr]">
        {/* Panneau de contexte — masqué en dessous de lg : sur mobile il pousserait
            le formulaire sous la ligne de flottaison, ce qui est le seul contenu
            réellement utile ici. */}
        <aside className="bg-muted/40 hidden flex-col justify-between border-e p-10 lg:flex xl:p-14">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldCheckIcon className="size-4" aria-hidden />
            Agent documentaire interne
          </div>

          <div className="max-w-[46ch] space-y-4">
            <h2 className="text-[clamp(1.5rem,1.1rem+1vw,2rem)] leading-tight font-semibold text-balance">
              Vos documents, filtrés par vos droits.
            </h2>
            <p className="text-muted-foreground text-[clamp(0.875rem,0.85rem+0.1vw,0.95rem)] leading-relaxed">
              L'agent ne cherche que dans les documents auxquels votre compte
              donne accès. Un document hors de vos groupes n'est jamais lu, jamais
              cité, jamais résumé.
            </p>
          </div>

          <p className="text-muted-foreground text-xs">
            Les conversations sont privées et rattachées à votre compte.
          </p>
        </aside>

        {/* Colonne formulaire — centrée, largeur fluide plafonnée, marges qui
            respirent à partir de la tablette. */}
        <main className="flex items-center justify-center px-5 py-10 pb-[max(2.5rem,env(safe-area-inset-bottom))] sm:px-8">
          <div className="w-full max-w-sm">
            <header className="mb-8 space-y-2">
              <div className="bg-primary/10 text-primary mb-5 flex size-11 items-center justify-center rounded-xl">
                <LockIcon className="size-5" aria-hidden />
              </div>
              <h1 className="text-[clamp(1.375rem,1.2rem+0.8vw,1.75rem)] leading-tight font-semibold">
                Connexion
              </h1>
              <p className="text-muted-foreground text-sm">
                Identifiez-vous pour accéder à l'agent et à vos conversations.
              </p>
            </header>

            <form onSubmit={onSubmit} noValidate className="space-y-5">
              <div className="space-y-2">
                <label htmlFor={emailId} className="text-sm font-medium">
                  Adresse e-mail
                </label>
                <Input
                  id={emailId}
                  type="email"
                  name="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="username"
                  autoFocus
                  required
                  disabled={pending}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? errorId : undefined}
                  // 44 px de haut : la cible tactile minimale recommandée.
                  className="h-11 text-base"
                  placeholder="vous@exemple.fr"
                />
              </div>

              <div className="space-y-2">
                <label htmlFor={passwordId} className="text-sm font-medium">
                  Mot de passe
                </label>
                <Input
                  id={passwordId}
                  type="password"
                  name="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  required
                  disabled={pending}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? errorId : undefined}
                  className="h-11 text-base"
                />
              </div>

              {/* `role="alert"` : l'erreur est annoncée aux lecteurs d'écran sans
                  qu'il faille déplacer le focus, qui doit rester dans le champ. */}
              {error && (
                <p
                  id={errorId}
                  role="alert"
                  className="border-destructive/30 bg-destructive/10 text-destructive rounded-lg border px-3 py-2 text-sm"
                >
                  {error}
                </p>
              )}

              <Button
                type="submit"
                disabled={pending || !email || !password}
                className="h-11 w-full text-base"
              >
                {pending && (
                  <LoaderCircleIcon
                    className="size-4 motion-safe:animate-spin"
                    aria-hidden
                  />
                )}
                {pending ? "Connexion…" : "Se connecter"}
              </Button>
            </form>

            {/* Même repliement statique que pour le préremplissage : en
                production, `false && …` disparaît, et `DemoPanel` avec lui. */}
            {import.meta.env.DEV && prefill && (
              <DemoPanel
                onPick={loginAs}
                disabled={pending}
                headingId={`${emailId}-demo`}
              />
            )}

            <p className="text-muted-foreground mt-8 text-xs leading-relaxed">
              Les comptes sont créés par un administrateur
              (<code className="font-mono">make user-create</code>). Il n'y a pas
              d'inscription libre.
            </p>
          </div>
        </main>
      </div>
    </div>
  );
};
