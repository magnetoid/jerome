import { Link, Navigate, NavLink, Outlet } from "react-router-dom";
import {
  ActivityIcon,
  ChevronsUpDownIcon,
  FolderIcon,
  PlusIcon,
  SettingsIcon,
  UsersIcon,
  type LucideIcon,
} from "lucide-react";
import { useTheme } from "next-themes";
import { useLogout, useMe } from "@/hooks/useMe";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

// Below lg the sidebar shows icons only; the label stays readable to screen readers.
const LABEL = "sr-only lg:not-sr-only";

function NavItem({ to, end, icon: Icon, children }: {
  to: string;
  end?: boolean;
  icon: LucideIcon;
  children: string;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      title={children}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
          "justify-center lg:justify-start",
          isActive
            ? "bg-accent text-accent-foreground"
            : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
        )
      }
    >
      <Icon className="size-4 shrink-0" aria-hidden />
      <span className={LABEL}>{children}</span>
    </NavLink>
  );
}

export default function AppShell() {
  const me = useMe();
  const logout = useLogout();
  const { theme, setTheme } = useTheme();

  if (me.isError) {
    // A 401 is already redirecting to /login; anything else needs to be visible.
    return (
      <main className="p-8">
        <p role="alert">Could not load your account. Try reloading the page.</p>
      </main>
    );
  }
  if (!me.data) return null;
  if (me.data.must_change_password) return <Navigate to="/change-password" replace />;
  const admin = me.data.role === "super_admin";

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 flex h-screen w-16 shrink-0 flex-col gap-4 border-r bg-muted/30 px-2 py-4 lg:w-56 lg:px-3">
        <Link to="/" className="px-1 text-lg font-semibold lg:px-3" title="Jerome">
          <span className="lg:hidden" aria-hidden>J</span>
          <span className={LABEL}>Jerome</span>
        </Link>
        <Button asChild size="sm" className="justify-center lg:justify-start">
          <Link to="/projects/new" title="New project">
            <PlusIcon aria-hidden />
            <span className={LABEL}>New project</span>
          </Link>
        </Button>
        <nav aria-label="Main" className="flex flex-col gap-1">
          <NavItem to="/" end icon={FolderIcon}>Projects</NavItem>
          <NavItem to="/activity" icon={ActivityIcon}>Activity</NavItem>
          {admin && <NavItem to="/team" icon={UsersIcon}>Team</NavItem>}
          {admin && <NavItem to="/settings" icon={SettingsIcon}>Settings</NavItem>}
        </nav>
        <div className="mt-auto">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="w-full justify-center lg:justify-between" title={me.data.name}>
                <span className="flex size-6 items-center justify-center rounded-full bg-muted text-xs font-semibold lg:hidden" aria-hidden>
                  {me.data.name.slice(0, 1).toUpperCase()}
                </span>
                <span className={cn(LABEL, "truncate")}>{me.data.name}</span>
                <ChevronsUpDownIcon className="hidden lg:block" aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" side="top" className="w-56">
              <DropdownMenuLabel>{me.data.name}</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem asChild>
                <Link to="/change-password">Change password</Link>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuLabel>Theme</DropdownMenuLabel>
              <DropdownMenuRadioGroup value={theme ?? "system"} onValueChange={setTheme}>
                <DropdownMenuRadioItem value="system">System</DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="light">Light</DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="dark">Dark</DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => void logout()}>Log out</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </aside>
      <div className="min-w-0 flex-1">
        <Outlet />
      </div>
    </div>
  );
}
