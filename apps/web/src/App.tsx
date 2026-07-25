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
import { Thread } from "@/components/assistant-ui/thread";
import { ToolFallback } from "@/components/assistant-ui/tool-fallback";
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

export default function App() {
  const health = useHealth();
  // La conversation ouverte vit dans l'URL (/ichat et /ichat/c/:id), pas dans un
  // état local : lien profond et boutons précédent/suivant fonctionnent d'office.
  const { threadId, openThread } = useChatRoute();

  // Même origine : Vite proxifie /api vers le service api (voir vite.config.ts).
  const transport = useMemo(() => new AssistantChatTransport({ api: "/api/chat" }), []);

  // apiBase vide → URLs relatives, donc same-origin pour l'historisation aussi.
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
}
