
import {
  RuntimeAdapterProvider,
  useAui,
  unstable_defaultDirectiveFormatter,
  type RemoteThreadListAdapter,
  type ThreadHistoryAdapter,
  type ThreadMessage,
} from "@assistant-ui/react";
import { createAssistantStream } from "assistant-stream";
import { useMemo, type PropsWithChildren } from "react";
import { toast } from "sonner";

const MAX_TITLE_LENGTH = 50;
const JSON_HEADERS = { "content-type": "application/json" };

export type ThreadScope = string;

function scopedUrl(url: string, scope: ThreadScope): string {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}scope=${encodeURIComponent(scope)}`;
}

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      ...JSON_HEADERS,
      ...init?.headers,
    },
  });
  if (!response.ok) {
    throw new Error(`Thread API ${response.status}: ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}

function errorMessage(error: unknown): string | undefined {
  return error instanceof Error ? error.message : undefined;
}

function buildTitle(messages: readonly ThreadMessage[]): string {
  const firstUserMessage = messages.find((m) => m.role === "user");
  const textPart = firstUserMessage?.content.find((p) => p.type === "text");
  if (!textPart || textPart.type !== "text") return "Nouvelle conversation";

  const plain = unstable_defaultDirectiveFormatter
    .parse(textPart.text)
    .map((segment) => (segment.kind === "text" ? segment.text : segment.label))
    .join("")
    .trim();

  if (!plain) return "Nouvelle conversation";
  return plain.length > MAX_TITLE_LENGTH
    ? `${plain.slice(0, MAX_TITLE_LENGTH - 3)}...`
    : plain;
}

export function useThreadHistoryAdapter(
  apiBase: string,
  scope: ThreadScope = "poc",
): ThreadHistoryAdapter {
  const aui = useAui();
  return useMemo<ThreadHistoryAdapter>(
    () => ({
      async load() {
        return { messages: [] };
      },
      async append() {},
      withFormat: (fmt) => ({
        async load() {
          const { remoteId } = aui.threadListItem().getState();
          if (!remoteId) return { messages: [] };
          const rows = await apiFetch<
            {
              id: string;
              parent_id: string | null;
              format: string;
              content: Record<string, unknown>;
            }[]
          >(scopedUrl(`${apiBase}/api/threads/${remoteId}/messages`, scope));
          return {
            messages: rows.map((row) =>
              fmt.decode({
                id: row.id,
                parent_id: row.parent_id,
                format: row.format,
                content: row.content as never,
              }),
            ),
          };
        },
        async append(item) {
          const { remoteId } = await aui.threadListItem().initialize();
          await apiFetch(
            scopedUrl(`${apiBase}/api/threads/${remoteId}/messages`, scope),
            {
              method: "POST",
              body: JSON.stringify({
                id: fmt.getId(item.message),
                parent_id: item.parentId,
                format: fmt.format,
                content: fmt.encode(item),
              }),
            },
          );
        },
      }),
    }),
    [apiBase, aui, scope],
  );
}

function ThreadHistoryProvider({
  apiBase,
  scope,
  children,
}: PropsWithChildren<{ apiBase: string; scope: ThreadScope }>) {
  const history = useThreadHistoryAdapter(apiBase, scope);

  return (
    <RuntimeAdapterProvider adapters={{ history }}>
      {children}
    </RuntimeAdapterProvider>
  );
}

export function createThreadListAdapter(
  apiBase: string,
  scope: ThreadScope = "poc",
): RemoteThreadListAdapter {
  return {
    list: async () => {
      const rows = await apiFetch<
        {
          id: string;
          title?: string | null;
          status: "regular" | "archived";
          updated_at?: string | null;
        }[]
      >(scopedUrl(`${apiBase}/api/threads`, scope));
      return {
        threads: rows.map((thread) => ({
          remoteId: thread.id,
          externalId: thread.id,
          status: thread.status,
          title: thread.title ?? undefined,
          lastMessageAt: thread.updated_at
            ? new Date(thread.updated_at)
            : undefined,
        })),
      };
    },
    rename: async (remoteId, title) => {
      try {
        await apiFetch(scopedUrl(`${apiBase}/api/threads/${remoteId}`, scope), {
          method: "PATCH",
          body: JSON.stringify({ title }),
        });
        toast.success("Conversation renommée", { description: title });
      } catch (error) {
        toast.error("Renommage impossible", {
          description: errorMessage(error),
        });
        throw error;
      }
    },
    updateCustom: async (remoteId, custom) => {
      await apiFetch(scopedUrl(`${apiBase}/api/threads/${remoteId}`, scope), {
        method: "PATCH",
        body: JSON.stringify({ custom }),
      });
    },
    archive: async (remoteId) => {
      try {
        await apiFetch(scopedUrl(`${apiBase}/api/threads/${remoteId}`, scope), {
          method: "PATCH",
          body: JSON.stringify({ status: "archived" }),
        });
        toast.success("Conversation archivée");
      } catch (error) {
        toast.error("Archivage impossible", {
          description: errorMessage(error),
        });
        throw error;
      }
    },
    unarchive: async (remoteId) => {
      try {
        await apiFetch(scopedUrl(`${apiBase}/api/threads/${remoteId}`, scope), {
          method: "PATCH",
          body: JSON.stringify({ status: "regular" }),
        });
        toast.success("Conversation désarchivée");
      } catch (error) {
        toast.error("Restauration impossible", {
          description: errorMessage(error),
        });
        throw error;
      }
    },
    delete: async (remoteId) => {
      try {
        const response = await fetch(
          scopedUrl(`${apiBase}/api/threads/${remoteId}`, scope),
          {
            method: "DELETE",
          },
        );
        if (!response.ok) {
          throw new Error(`Thread API ${response.status}`);
        }
        toast.success("Conversation supprimée");
      } catch (error) {
        toast.error("Suppression impossible", {
          description: errorMessage(error),
        });
        throw error;
      }
    },
    initialize: async (threadId) => {
      const thread = await apiFetch<{ id: string }>(`${apiBase}/api/threads`, {
        method: "POST",
        body: JSON.stringify({ id: threadId, scope }),
      });
      return { remoteId: thread.id, externalId: thread.id };
    },
    fetch: async (remoteId) => {
      const thread = await apiFetch<{
        id: string;
        title?: string | null;
        status: "regular" | "archived";
        updated_at?: string | null;
        custom?: Record<string, unknown> | null;
      }>(scopedUrl(`${apiBase}/api/threads/${remoteId}`, scope));
      return {
        remoteId: thread.id,
        externalId: thread.id,
        status: thread.status,
        title: thread.title ?? undefined,
        lastMessageAt: thread.updated_at
          ? new Date(thread.updated_at)
          : undefined,
        custom: thread.custom ?? undefined,
      };
    },
    generateTitle: async (
      remoteId: string,
      messages: readonly ThreadMessage[],
    ) => {
      const title = buildTitle(messages);
      await apiFetch(scopedUrl(`${apiBase}/api/threads/${remoteId}`, scope), {
        method: "PATCH",
        body: JSON.stringify({ title }),
      });
      return createAssistantStream((controller) => {
        controller.appendText(title);
      });
    },
    unstable_Provider({ children }) {
      return (
        <ThreadHistoryProvider apiBase={apiBase} scope={scope}>
          {children}
        </ThreadHistoryProvider>
      );
    },
  };
}
