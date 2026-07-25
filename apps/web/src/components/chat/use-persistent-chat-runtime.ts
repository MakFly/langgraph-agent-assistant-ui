
import type { UIMessage } from "@ai-sdk/react";
import { type AssistantRuntime } from "@assistant-ui/core";
import { useRemoteThreadListRuntime } from "@assistant-ui/react";
import {
  useChatRuntime,
  type UseChatRuntimeOptions,
} from "@assistant-ui/react-ai-sdk";
import { useMemo } from "react";
import {
  createThreadListAdapter,
  type ThreadScope,
  useThreadHistoryAdapter,
} from "@/components/chat/thread-list-adapter";

type PersistentChatRuntimeOptions<UI_MESSAGE extends UIMessage = UIMessage> =
  Omit<UseChatRuntimeOptions<UI_MESSAGE>, "cloud"> & {
    apiBase: string;
    scope?: ThreadScope;
    threadId?: string;
  };

export function usePersistentChatRuntime<
  UI_MESSAGE extends UIMessage = UIMessage,
>(options: PersistentChatRuntimeOptions<UI_MESSAGE>): AssistantRuntime {
  const {
    apiBase,
    onThreadIdChange,
    scope = "poc",
    threadId,
    ...chatOptions
  } = options;
  const adapter = useMemo(
    () => createThreadListAdapter(apiBase, scope),
    [apiBase, scope],
  );

  return useRemoteThreadListRuntime({
    runtimeHook: function RuntimeHook() {
      const history = useThreadHistoryAdapter(apiBase, scope);
      return useChatRuntime({
        ...chatOptions,
        adapters: {
          ...chatOptions.adapters,
          history,
        },
      });
    },
    adapter,
    threadId,
    onThreadIdChange,
  });
}
