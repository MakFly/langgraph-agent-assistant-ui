import { useCallback, useMemo } from "react";
import type { FC, PropsWithChildren } from "react";
import {
  AssistantRuntimeProvider,
  AuiProvider,
  Suggestions,
  useAui,
  type SuggestionConfig,
} from "@assistant-ui/react";
import { AssistantChatTransport } from "@assistant-ui/react-ai-sdk";
import { LoaderCircleIcon } from "lucide-react";
import { Thread } from "@/components/assistant-ui/thread";
import { ToolFallback } from "@/components/assistant-ui/tool-fallback";
import { AuthProvider, useAuth } from "@/components/auth/auth-context";
import { LoginScreen } from "@/components/auth/login-screen";
import { usePersistentChatRuntime } from "@/components/chat/use-persistent-chat-runtime";
import { SettingsProvider } from "@/components/settings/settings-context";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Welcome } from "@/components/welcome";
import { AppNavbar } from "@/components/xulux/app-navbar";
import { AppSidebar } from "@/components/xulux/app-sidebar";
import { useChatRoute } from "@/hooks/use-chat-route";
import { useHealth } from "@/hooks/use-health";

/** Chaque suggestion vise un outil différent — c'est la démo en un clic. */
const SUGGESTIONS: SuggestionConfig[] = [
  {
    title: "Documents internes",
    label: "contrats et procédures",
    prompt:
      "Le client a livré un chantier et le carrelage est moche mais tient bien : est-ce couvert par la décennale ?",
  },
  {
    title: "Météo",
    label: "Lyon, 3 jours",
    prompt: "Quel temps fera-t-il à Lyon dans les 3 prochains jours ?",
  },
  {
    title: "Recherche",
    label: "Wikipédia + Hacker News",
    prompt: "Qu'est-ce que LangGraph ? Et qu'en dit Hacker News récemment ?",
  },
  {
    title: "Calcul",
    label: "pourcentage + conversion",
    prompt: "Calcule 18 % de 2340 €, puis convertis 90 km/h en m/s.",
  },
];

/**
 * Le scope `suggestions` est indépendant du runtime : c'est lui que lit
 * <ThreadPrimitive.Suggestions>, pas une option passée au runtime.
 */
const WithSuggestions: FC<PropsWithChildren> = ({ children }) => {
  const aui = useAui({ suggestions: Suggestions(SUGGESTIONS) });
  return <AuiProvider value={aui}>{children}</AuiProvider>;
};

/** Attente de la vérification de session — quelques dizaines de ms en pratique. */
const Splash: FC = () => (
  <div className="bg-background text-muted-foreground flex min-h-dvh items-center justify-center">
    <LoaderCircleIcon className="size-5 motion-safe:animate-spin" aria-hidden />
    <span className="sr-only">Vérification de la session…</span>
  </div>
);

/**
 * L'application de chat elle-même.
 *
 * Séparée de la porte d'entrée à dessein : le runtime de conversation n'est
 * monté qu'une fois l'utilisateur connu. Sinon il chargerait la liste des
 * conversations avant l'authentification, prendrait un 401, et afficherait une
 * erreur là où il n'y a qu'une session à ouvrir.
 */
const ChatApp: FC = () => {
  const health = useHealth();
  // La conversation ouverte vit dans l'URL (/ichat et /ichat/c/:id), pas dans un
  // état local : lien profond et boutons précédent/suivant fonctionnent d'office.
  const { threadId, openThread } = useChatRoute();

  // Même origine : Vite proxifie /api vers le service api (voir vite.config.ts).
  const transport = useMemo(() => new AssistantChatTransport({ api: "/api/chat" }), []);

  // apiBase vide → URLs relatives, donc same-origin pour l'historisation aussi.
  // Le cookie de session part avec, sans configuration particulière.
  const runtime = usePersistentChatRuntime({
    apiBase: "",
    scope: "poc",
    transport,
    threadId,
    onThreadIdChange: openThread,
  });

  const onNewThread = useCallback(() => openThread(), [openThread]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <WithSuggestions>
        {/* Un seul état de configuration, partagé par le panneau (sidebar) et le
            sélecteur de modèle du composer. */}
        <SettingsProvider>
          <TooltipProvider>
            {/* Coque reprise de xulux : SidebarProvider + SidebarInset. */}
            <div className="overflow-hidden">
              <SidebarProvider defaultOpen className="relative h-dvh">
                <AppSidebar health={health} />
                <SidebarInset className="bg-sidebar shadow-none md:rounded-none md:peer-data-[variant=inset]:rounded-none md:peer-data-[variant=inset]:shadow-none">
                  <div className="bg-sidebar flex flex-1 flex-col overflow-hidden">
                    <AppNavbar
                      title="Agent à outils gratuits"
                      health={health}
                      onNewThread={onNewThread}
                    />
                    <main className="bg-background min-h-0 flex-1 overflow-hidden rounded-t-xl border-t pb-[env(safe-area-inset-bottom)]">
                      <Thread components={{ Welcome, ToolFallback }} />
                    </main>
                  </div>
                </SidebarInset>
              </SidebarProvider>
            </div>
            <Toaster />
          </TooltipProvider>
        </SettingsProvider>
      </WithSuggestions>
    </AssistantRuntimeProvider>
  );
};

/** Porte d'entrée : rien de l'application n'est monté sans session valide. */
const AuthGate: FC = () => {
  const auth = useAuth();

  if (auth.state === "loading") return <Splash />;
  if (auth.state === "anonymous") return <LoginScreen />;
  return <ChatApp />;
};

export default function App() {
  return (
    <AuthProvider>
      <AuthGate />
    </AuthProvider>
  );
}
