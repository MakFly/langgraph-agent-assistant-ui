import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function CustomSidebarTrigger({
	className,
}: {
	className?: string;
}) {
	return (
		<Tooltip delayDuration={1000}>
			<TooltipTrigger asChild>
				{/* pointer-coarse : 44px de cible sur appareil tactile, sans grossir
				    l'UI à la souris. Un breakpoint de largeur ne suffirait pas — une
				    tablette à 768px est tactile. */}
				<SidebarTrigger className={cn("pointer-coarse:size-11", className)} />
			</TooltipTrigger>
			<TooltipContent className="px-2 py-1" side="right">
				Toggle Sidebar{" "}
				<KbdGroup>
					<Kbd>⌘</Kbd>
					<Kbd>b</Kbd>
				</KbdGroup>
			</TooltipContent>
		</Tooltip>
	);
}
