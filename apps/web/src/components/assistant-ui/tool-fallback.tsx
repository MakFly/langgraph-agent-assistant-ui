"use client";

import { memo, useCallback, useRef, useState } from "react";
import {
  AlertCircleIcon,
  CheckIcon,
  ChevronDownIcon,
  LoaderIcon,
  XCircleIcon,
} from "lucide-react";
import {
  useScrollLock,
  useToolCallElapsed,
  type ToolApprovalOption,
  type ToolCallMessagePart,
  type ToolCallMessagePartProps,
  type ToolCallMessagePartStatus,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { resolveToolMeta, summarizeToolCall } from "@/lib/tool-metadata";
import { useToolDetails } from "@/hooks/use-tool-details";

const ANIMATION_DURATION = 200;

const pressable = "active:scale-[0.98]";

export type ToolFallbackRootProps = Omit<
  React.ComponentProps<typeof Collapsible>,
  "open" | "onOpenChange"
> & {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  defaultOpen?: boolean;
};

function ToolFallbackRoot({
  className,
  open: controlledOpen,
  onOpenChange: controlledOnOpenChange,
  defaultOpen = false,
  children,
  ...props
}: ToolFallbackRootProps) {
  const collapsibleRef = useRef<HTMLDivElement>(null);
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const lockScroll = useScrollLock(collapsibleRef, ANIMATION_DURATION);

  const isControlled = controlledOpen !== undefined;
  const isOpen = isControlled ? controlledOpen : uncontrolledOpen;

  const handleOpenChange = useCallback(
    (open: boolean) => {
      lockScroll();
      if (!isControlled) {
        setUncontrolledOpen(open);
      }
      controlledOnOpenChange?.(open);
    },
    [lockScroll, isControlled, controlledOnOpenChange],
  );

  return (
    <Collapsible
      ref={collapsibleRef}
      data-slot="tool-fallback-root"
      open={isOpen}
      onOpenChange={handleOpenChange}
      className={cn(
        "aui-tool-fallback-root group/tool-fallback-root w-full",
        className,
      )}
      style={
        {
          "--animation-duration": `${ANIMATION_DURATION}ms`,
        } as React.CSSProperties
      }
      {...props}
    >
      {children}
    </Collapsible>
  );
}

type ToolStatus = ToolCallMessagePartStatus["type"];

const statusIconMap: Record<ToolStatus, React.ElementType> = {
  running: LoaderIcon,
  complete: CheckIcon,
  incomplete: XCircleIcon,
  "requires-action": AlertCircleIcon,
};

const formatToolDuration = (ms: number) => {
  if (ms < 1000) return "<1s";
  const seconds = ms / 1000;
  if (seconds < 10) return `${(Math.floor(seconds * 10) / 10).toFixed(1)}s`;
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
};

function ToolFallbackDuration({
  className,
  ...props
}: React.ComponentProps<"span">) {
  const elapsedMs = useToolCallElapsed();
  if (elapsedMs === undefined) return null;

  return (
    <span
      data-slot="tool-fallback-duration"
      className={cn(
        "aui-tool-fallback-duration text-muted-foreground text-xs tabular-nums",
        className,
      )}
      {...props}
    >
      {formatToolDuration(elapsedMs)}
    </span>
  );
}

// Un badge de statut n'apparaît que lorsqu'il apporte une info : un ✓ vert sur
// chaque outil réussi alourdit le fil (les meilleures UIs agentiques ne le font
// pas). État nominal terminé = pas de badge, juste l'icône de l'outil.
const statusBadgeTone: Partial<Record<ToolStatus, string>> = {
  running: "text-muted-foreground",
  incomplete: "text-destructive",
  "requires-action": "text-amber-500",
};

// Partie visuelle commune (pastille + libellé + aperçu), partagée par la ligne
// cliquable (mode technique, avec chevron) et la ligne statique (mode propre).
function ToolFallbackLead({
  toolName,
  argsText,
  result,
  status,
}: {
  toolName: string;
  argsText?: string;
  result?: unknown;
  status?: ToolCallMessagePartStatus;
}) {
  const statusType = status?.type ?? "complete";
  const isRunning = statusType === "running";
  const isCancelled =
    status?.type === "incomplete" && status.reason === "cancelled";

  const meta = resolveToolMeta(toolName);
  const ToolIcon = meta.icon;
  const StatusIcon = statusIconMap[statusType];
  // Aperçu : requête + résultat (« 2340 * 0.18 = 421,2 ») dès qu'il est arrivé.
  const summary = summarizeToolCall(toolName, argsText, result);
  // En cours : formulation d'action (« Recherche sur Wikipédia ») ; sinon le nom
  // de la source (« Wikipédia »). L'aperçu de la requête complète la ligne.
  const label = isRunning && !isCancelled ? meta.action : meta.label;
  const badgeTone = isCancelled
    ? "text-muted-foreground"
    : statusBadgeTone[statusType];

  return (
    <>
      {/* Pastille de l'outil + badge de statut en incrustation. */}
      <span
        data-slot="tool-fallback-trigger-icon"
        className={cn(
          "aui-tool-fallback-trigger-icon relative grid size-6 shrink-0 place-items-center rounded-md border transition-colors",
          isCancelled
            ? "text-muted-foreground bg-muted/50 border-transparent"
            : "text-foreground/80 bg-muted border-border/60",
        )}
      >
        <ToolIcon className="size-3.5" />
        {badgeTone && (
          <span
            aria-hidden
            className={cn(
              "bg-background absolute -right-1 -bottom-1 grid size-3.5 place-items-center rounded-full",
              badgeTone,
            )}
          >
            <StatusIcon
              className={cn(
                "size-3",
                isRunning && "animate-spin [animation-duration:0.6s]",
              )}
            />
          </span>
        )}
      </span>

      <span
        data-slot="tool-fallback-trigger-label"
        className={cn(
          "aui-tool-fallback-trigger-label-wrapper flex min-w-0 grow items-baseline gap-1.5 leading-none",
          isCancelled && "line-through",
        )}
      >
        <span className="relative shrink-0 font-medium">
          {label}
          {isRunning && (
            <span
              aria-hidden
              data-slot="tool-fallback-trigger-shimmer"
              className="aui-tool-fallback-trigger-shimmer shimmer pointer-events-none absolute inset-0 motion-reduce:animate-none"
            >
              {label}
            </span>
          )}
        </span>
        {summary && (
          <span className="text-muted-foreground min-w-0 truncate font-normal">
            <span className="opacity-40">·</span> {summary}
          </span>
        )}
      </span>
    </>
  );
}

function ToolFallbackTrigger({
  toolName,
  argsText,
  result,
  status,
  className,
  ...props
}: React.ComponentProps<typeof CollapsibleTrigger> & {
  toolName: string;
  argsText?: string;
  result?: unknown;
  status?: ToolCallMessagePartStatus;
}) {
  return (
    <CollapsibleTrigger
      data-slot="tool-fallback-trigger"
      className={cn(
        "aui-tool-fallback-trigger group/trigger -mx-1.5 flex w-[calc(100%+0.75rem)] items-center gap-2.5 rounded-lg px-1.5 py-1 text-start text-sm transition-colors active:scale-[0.995]",
        "hover:bg-muted/50",
        className,
      )}
      {...props}
    >
      <ToolFallbackLead
        toolName={toolName}
        argsText={argsText}
        result={result}
        status={status}
      />
      <ToolFallbackDuration className="ms-auto ps-1" />
      <ChevronDownIcon
        data-slot="tool-fallback-trigger-chevron"
        className={cn(
          "aui-tool-fallback-trigger-chevron text-muted-foreground size-4 shrink-0",
          "transition-transform duration-(--animation-duration) ease-[cubic-bezier(0.32,0.72,0,1)] motion-reduce:transition-none",
          "-rotate-90",
          "group-data-open/trigger:rotate-0",
          "group-data-panel-open/trigger:rotate-0",
        )}
      />
    </CollapsibleTrigger>
  );
}

// Mode « propre » : même ligne d'activité, mais informative (ni cliquable, ni
// dépliable) — aucun JSON. C'est ce que voit un utilisateur non technique.
function ToolFallbackStatic({
  toolName,
  argsText,
  result,
  status,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  toolName: string;
  argsText?: string;
  result?: unknown;
  status?: ToolCallMessagePartStatus;
}) {
  return (
    <div
      data-slot="tool-fallback-static"
      className={cn(
        "aui-tool-fallback-static flex w-full items-center gap-2.5 py-1 text-sm",
        className,
      )}
      {...props}
    >
      <ToolFallbackLead
        toolName={toolName}
        argsText={argsText}
        result={result}
        status={status}
      />
      <ToolFallbackDuration className="ms-auto ps-1" />
    </div>
  );
}

function ToolFallbackContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof CollapsibleContent>) {
  return (
    <CollapsibleContent
      data-slot="tool-fallback-content"
      className={cn(
        "aui-tool-fallback-content relative overflow-hidden text-sm outline-none",
        "group/collapsible-content ease-[cubic-bezier(0.32,0.72,0,1)] motion-reduce:animate-none",
        "data-closed:animate-collapsible-up",
        "data-open:animate-collapsible-down",
        "data-closed:fill-mode-forwards",
        "data-closed:pointer-events-none",
        "data-open:duration-(--animation-duration)",
        "data-closed:duration-(--animation-duration)",
        className,
      )}
      {...props}
    >
      <div
        className={cn(
          "flex flex-col gap-2 ps-6 pt-1 pb-2 ease-[cubic-bezier(0.32,0.72,0,1)] motion-reduce:animate-none",
          "group-data-open/collapsible-content:animate-in group-data-open/collapsible-content:fade-in-0 group-data-open/collapsible-content:blur-in-[2px] group-data-open/collapsible-content:slide-in-from-top-1",
          "group-data-closed/collapsible-content:animate-out group-data-closed/collapsible-content:fade-out-0 group-data-closed/collapsible-content:blur-out-[2px] group-data-closed/collapsible-content:slide-out-to-top-1",
          "group-data-closed/collapsible-content:duration-(--animation-duration) group-data-open/collapsible-content:duration-(--animation-duration)",
        )}
      >
        {children}
      </div>
    </CollapsibleContent>
  );
}

const renderArgValue = (value: unknown): string =>
  typeof value === "string" ? value : JSON.stringify(value);

function ToolFallbackArgs({
  argsText,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  argsText?: string;
}) {
  if (!argsText) return null;

  // Rendu clé/valeur quand les arguments sont un objet JSON simple (le cas de
  // tous nos outils) ; repli sur le texte brut sinon (JSON partiel en cours de
  // streaming, tableau, valeur scalaire…).
  let entries: [string, unknown][] | null = null;
  try {
    const parsed = JSON.parse(argsText);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      entries = Object.entries(parsed);
    }
  } catch {
    entries = null;
  }

  return (
    <div
      data-slot="tool-fallback-args"
      className={cn("aui-tool-fallback-args", className)}
      {...props}
    >
      <p className="aui-tool-fallback-args-header text-muted-foreground mb-1 text-xs font-medium">
        Paramètres
      </p>
      {entries && entries.length > 0 ? (
        <dl className="aui-tool-fallback-args-list bg-muted/50 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-md p-2.5 text-xs">
          {entries.map(([key, value]) => (
            <div key={key} className="col-span-2 grid grid-cols-subgrid">
              <dt className="text-muted-foreground font-mono">{key}</dt>
              <dd className="text-foreground/90 min-w-0 break-words whitespace-pre-wrap">
                {renderArgValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <pre className="aui-tool-fallback-args-value bg-muted/50 text-foreground/90 rounded-md p-2.5 text-xs whitespace-pre-wrap">
          {argsText}
        </pre>
      )}
    </div>
  );
}

function ToolFallbackResult({
  result,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  result?: unknown;
}) {
  if (result === undefined) return null;

  return (
    <div
      data-slot="tool-fallback-result"
      className={cn("aui-tool-fallback-result", className)}
      {...props}
    >
      <p className="aui-tool-fallback-result-header text-muted-foreground text-xs font-medium">
        Résultat
      </p>
      <pre className="aui-tool-fallback-result-content bg-muted/50 text-foreground/90 mt-1 max-h-64 overflow-auto rounded-md p-2.5 text-xs whitespace-pre-wrap">
        {typeof result === "string" ? result : JSON.stringify(result, null, 2)}
      </pre>
    </div>
  );
}

function ToolFallbackError({
  status,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  status?: ToolCallMessagePartStatus;
}) {
  if (status?.type !== "incomplete") return null;

  const error = status.error;
  const errorText = error
    ? typeof error === "string"
      ? error
      : JSON.stringify(error)
    : null;

  if (!errorText) return null;

  const isCancelled = status.reason === "cancelled";
  const headerText = isCancelled ? "Cancelled reason:" : "Error:";

  return (
    <div
      data-slot="tool-fallback-error"
      className={cn("aui-tool-fallback-error", className)}
      {...props}
    >
      <p className="aui-tool-fallback-error-header text-muted-foreground font-semibold">
        {headerText}
      </p>
      <p className="aui-tool-fallback-error-reason text-muted-foreground">
        {errorText}
      </p>
    </div>
  );
}

const APPROVED_RESULT = "Approved by user";
const DENIED_RESULT = "User denied tool execution";

const APPROVAL_OPTION_DEFAULT_LABELS: Record<string, string> = {
  "allow-once": "Allow",
  "allow-always": "Always allow",
  "reject-once": "Deny",
  "reject-always": "Always deny",
};

const isAllowKind = (kind: string) =>
  kind === "allow-once" || kind === "allow-always";

const approvalOptionLabel = (option: ToolApprovalOption) =>
  option.label ??
  (Object.hasOwn(APPROVAL_OPTION_DEFAULT_LABELS, option.kind)
    ? APPROVAL_OPTION_DEFAULT_LABELS[option.kind]
    : undefined) ??
  option.id;

function ToolFallbackApproval({
  className,
  addResult,
  resume,
  interrupt,
  approval,
  respondToApproval,
  ...props
}: React.ComponentProps<"div"> &
  Partial<
    Pick<ToolCallMessagePartProps, "addResult" | "resume" | "respondToApproval">
  > & {
    interrupt?: ToolCallMessagePart["interrupt"];
    approval?: ToolCallMessagePart["approval"];
  }) {
  const [submitted, setSubmitted] = useState(false);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  if (
    approval != null &&
    (approval.approved !== undefined || approval.resolution !== undefined)
  )
    return null;

  // Custom (`_`-prefixed) kinds cannot be resolved to a boolean by the kit;
  // hosts using custom kinds render their own bar. A declared option list is
  // a host constraint: the kit never adds an approval path beyond it, but
  // always preserves a refusal path.
  const declaredOptions = respondToApproval ? approval?.options : undefined;
  const options = declaredOptions?.filter((o) =>
    Object.hasOwn(APPROVAL_OPTION_DEFAULT_LABELS, o.kind),
  );

  const respond = (approved: boolean) => {
    if (submitted) return;
    if (
      approval != null &&
      approval.approved === undefined &&
      respondToApproval
    ) {
      respondToApproval({ approved });
    } else if (interrupt) {
      resume?.({ approved });
    } else {
      addResult?.(approved ? APPROVED_RESULT : DENIED_RESULT);
    }
    setSubmitted(true);
  };

  const respondWithOption = (option: ToolApprovalOption) => {
    if (submitted) return;
    respondToApproval?.({ optionId: option.id });
    setSubmitted(true);
    setConfirmingId(null);
  };

  const handleOption = (option: ToolApprovalOption) => {
    if (option.confirm) {
      setConfirmingId(option.id);
    } else {
      respondWithOption(option);
    }
  };

  const confirming =
    confirmingId != null
      ? options?.find((o) => o.id === confirmingId)
      : undefined;

  if (confirming) {
    const confirmMeta =
      typeof confirming.confirm === "object" ? confirming.confirm : undefined;
    const confirmDescription =
      confirmMeta?.description ?? confirming.description;
    return (
      <div
        data-slot="tool-fallback-approval-confirm"
        className={cn(
          "aui-tool-fallback-approval-confirm flex flex-col gap-2 pt-1",
          className,
        )}
        {...props}
      >
        <p className="aui-tool-fallback-approval-confirm-title font-semibold">
          {confirmMeta?.title ?? `${approvalOptionLabel(confirming)}?`}
        </p>
        {confirmDescription && (
          <p className="aui-tool-fallback-approval-confirm-description text-muted-foreground">
            {confirmDescription}
          </p>
        )}
        {confirming.grants && confirming.grants.length > 0 && (
          <ul className="aui-tool-fallback-approval-confirm-grants flex flex-col gap-1">
            {confirming.grants.map((grant) => (
              <li key={grant}>
                <code className="aui-tool-fallback-approval-confirm-grant bg-muted rounded px-1.5 py-0.5 text-xs">
                  {grant}
                </code>
              </li>
            ))}
          </ul>
        )}
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            className={pressable}
            onClick={() => respondWithOption(confirming)}
            disabled={submitted}
          >
            Confirm
          </Button>
          <Button
            size="sm"
            variant="outline"
            className={pressable}
            onClick={() => setConfirmingId(null)}
            disabled={submitted}
          >
            Back
          </Button>
        </div>
      </div>
    );
  }

  if (declaredOptions && declaredOptions.length > 0) {
    const allowOptions = options?.filter((o) => isAllowKind(o.kind)) ?? [];
    const rejectOptions = options?.filter((o) => !isAllowKind(o.kind)) ?? [];
    return (
      <div
        data-slot="tool-fallback-approval"
        className={cn(
          "aui-tool-fallback-approval flex flex-wrap items-center gap-2 pt-1",
          className,
        )}
        {...props}
      >
        {[...allowOptions, ...rejectOptions].map((option) => (
          <Button
            key={option.id}
            size="sm"
            variant={option === allowOptions[0] ? "default" : "outline"}
            className={pressable}
            onClick={() => handleOption(option)}
            disabled={submitted}
          >
            {approvalOptionLabel(option)}
          </Button>
        ))}
        {rejectOptions.length === 0 && (
          <Button
            size="sm"
            variant="outline"
            className={pressable}
            onClick={() => respond(false)}
            disabled={submitted}
          >
            Deny
          </Button>
        )}
      </div>
    );
  }

  return (
    <div
      data-slot="tool-fallback-approval"
      className={cn(
        "aui-tool-fallback-approval flex items-center gap-2 pt-1",
        className,
      )}
      {...props}
    >
      <Button
        size="sm"
        className={pressable}
        onClick={() => respond(true)}
        disabled={submitted}
      >
        Allow
      </Button>
      <Button
        size="sm"
        variant="outline"
        className={pressable}
        onClick={() => respond(false)}
        disabled={submitted}
      >
        Deny
      </Button>
    </div>
  );
}

const ToolFallbackImpl: ToolCallMessagePartComponent = ({
  toolName,
  argsText,
  result,
  status,
  addResult,
  resume,
  interrupt,
  approval,
  respondToApproval,
}) => {
  const { enabled: showDetails } = useToolDetails();
  const isCancelled =
    status?.type === "incomplete" && status.reason === "cancelled";
  const isRequiresAction = status?.type === "requires-action";

  const [open, setOpen] = useState(isRequiresAction);
  const [prevRequiresAction, setPrevRequiresAction] =
    useState(isRequiresAction);
  if (isRequiresAction !== prevRequiresAction) {
    setPrevRequiresAction(isRequiresAction);
    if (isRequiresAction) setOpen(true);
  }

  // Vue « propre » (défaut) : ligne d'activité lisible, sans args ni résultat
  // bruts. La validation reste proposée — autoriser/refuser ne doit pas exiger
  // de lire du JSON.
  if (!showDetails) {
    return (
      <div
        data-slot="tool-fallback-clean"
        className="aui-tool-fallback-clean flex flex-col gap-1"
      >
        <ToolFallbackStatic
          toolName={toolName}
          argsText={argsText}
          result={result}
          status={status}
        />
        {isRequiresAction && (
          <ToolFallbackApproval
            className="ps-[2.125rem]"
            addResult={addResult}
            resume={resume}
            interrupt={interrupt}
            approval={approval}
            respondToApproval={respondToApproval}
          />
        )}
      </div>
    );
  }

  return (
    <ToolFallbackRoot open={open} onOpenChange={setOpen}>
      <ToolFallbackTrigger
        toolName={toolName}
        argsText={argsText}
        result={result}
        status={status}
      />
      <ToolFallbackContent>
        <ToolFallbackError status={status} />
        <ToolFallbackArgs
          argsText={argsText}
          className={cn(isCancelled && "opacity-60")}
        />
        {isRequiresAction && (
          <ToolFallbackApproval
            addResult={addResult}
            resume={resume}
            interrupt={interrupt}
            approval={approval}
            respondToApproval={respondToApproval}
          />
        )}
        {!isCancelled && <ToolFallbackResult result={result} />}
      </ToolFallbackContent>
    </ToolFallbackRoot>
  );
};

const ToolFallback = memo(
  ToolFallbackImpl,
) as unknown as ToolCallMessagePartComponent & {
  Root: typeof ToolFallbackRoot;
  Trigger: typeof ToolFallbackTrigger;
  Content: typeof ToolFallbackContent;
  Args: typeof ToolFallbackArgs;
  Result: typeof ToolFallbackResult;
  Error: typeof ToolFallbackError;
  Approval: typeof ToolFallbackApproval;
};

ToolFallback.displayName = "ToolFallback";
ToolFallback.Root = ToolFallbackRoot;
ToolFallback.Trigger = ToolFallbackTrigger;
ToolFallback.Content = ToolFallbackContent;
ToolFallback.Args = ToolFallbackArgs;
ToolFallback.Result = ToolFallbackResult;
ToolFallback.Error = ToolFallbackError;
ToolFallback.Approval = ToolFallbackApproval;

export {
  ToolFallback,
  ToolFallbackRoot,
  ToolFallbackTrigger,
  ToolFallbackContent,
  ToolFallbackArgs,
  ToolFallbackResult,
  ToolFallbackError,
  ToolFallbackApproval,
};
