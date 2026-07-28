
import {
	Avatar,
	AvatarFallback,
} from "@/components/ui/avatar";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuGroup,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { UserIcon, BellIcon, CommandIcon, LifeBuoyIcon, GraduationCapIcon, CreditCardIcon, LogOutIcon } from "lucide-react";

import { useAuth } from "@/components/auth/auth-context";

export function NavUser() {
	const auth = useAuth();

	// Ce composant n'est monté que dans l'application, donc derrière la porte
	// d'authentification. Le repli couvre le seul cas restant : une session qui
	// expire pendant que le menu est ouvert.
	const user =
		auth.state === "authenticated"
			? auth.user
			: { email: "session expirée", display_name: null, role: "member" as const, groups: [] };

	const name = user.display_name || user.email;
	// Pas d'avatar distant : une photo servie par un tiers, c'est une requête qui
	// signale à ce tiers qui utilise l'application, et quand.
	const initials = name.slice(0, 2).toUpperCase();

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Avatar className="size-8 cursor-pointer">
					<AvatarFallback>{initials}</AvatarFallback>
				</Avatar>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="end" className="w-60">
				<DropdownMenuItem className="flex items-center justify-start gap-2">
					<DropdownMenuLabel className="flex min-w-0 items-center gap-3">
						<Avatar className="size-10">
							<AvatarFallback>{initials}</AvatarFallback>
						</Avatar>
						<div className="min-w-0">
							<span className="font-medium text-foreground">{name}</span>{" "}
							<br />
							<div className="max-w-full overflow-hidden overflow-ellipsis whitespace-nowrap text-muted-foreground text-xs">
								{user.email}
							</div>
						</div>
					</DropdownMenuLabel>
				</DropdownMenuItem>
				<DropdownMenuSeparator />
				{/* Rôle et groupes : c'est exactement ce qui décide de ce que l'agent
				    a le droit de chercher. L'afficher évite le « pourquoi il ne
				    trouve pas ce document ? » qui n'a sinon aucune réponse visible. */}
				<div className="px-2 py-1.5 text-xs text-muted-foreground">
					<div>
						rôle : <span className="text-foreground">{user.role}</span>
					</div>
					<div className="mt-0.5 break-words">
						groupes : <span className="text-foreground">{user.groups.join(", ") || "—"}</span>
					</div>
				</div>
				<DropdownMenuSeparator />
				<DropdownMenuGroup>
					<DropdownMenuItem>
						<UserIcon
						/>
						Profile
					</DropdownMenuItem>
				</DropdownMenuGroup>
				<DropdownMenuSeparator />
				<DropdownMenuGroup>
					<DropdownMenuItem>
						<BellIcon
						/>
						Notifications
					</DropdownMenuItem>
					<DropdownMenuItem>
						<CommandIcon
						/>
						Keyboard shortcuts
					</DropdownMenuItem>
				</DropdownMenuGroup>
				<DropdownMenuSeparator />
				<DropdownMenuGroup>
					<DropdownMenuItem>
						<LifeBuoyIcon
						/>
						Help center
					</DropdownMenuItem>
					<DropdownMenuItem asChild>
						<a href="#">
							<GraduationCapIcon />
							Agent training
						</a>
					</DropdownMenuItem>
				</DropdownMenuGroup>
				<DropdownMenuSeparator />
				<DropdownMenuGroup>
					<DropdownMenuItem>
						<CreditCardIcon
						/>
						Subscription
					</DropdownMenuItem>
				</DropdownMenuGroup>
				<DropdownMenuSeparator />
				<DropdownMenuGroup>
					<DropdownMenuItem
						className="w-full cursor-pointer"
						onSelect={() => {
							void auth.logout();
						}}
						variant="destructive"
					>
						<LogOutIcon
						/>
						Se déconnecter
					</DropdownMenuItem>
				</DropdownMenuGroup>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
