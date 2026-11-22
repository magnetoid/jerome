import { Link, Navigate, NavLink, Outlet } from "react-router-dom";
import { ChevronDownIcon } from "lucide-react";
import { useLogout, useMe } from "@/hooks/useMe";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const link = ({ isActive }: { isActive: boolean }) =>
  cn(
    "text-sm font-medium transition-colors hover:text-foreground",
    isActive ? "text-foreground" : "text-muted-foreground",
  );

export default function AppShell() {
  const me = useMe();
  const logout = useLogout();

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

  return (
    <div className="min-h-screen">
      <header className="border-b">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
          <Link to="/" className="text-lg font-semibold">
            Jerome
          </Link>
          <nav aria-label="Main" className="flex items-center gap-4">
            <NavLink to="/" end className={link}>
              Projects
            </NavLink>
            <NavLink to="/team" className={link}>
              Team
            </NavLink>
          </nav>
          <div className="ml-auto">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm">
                  {me.data.name}
                  <ChevronDownIcon />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>{me.data.name}</DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link to="/change-password">Change password</Link>
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => void logout()}>Log out</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </header>
      <Outlet />
    </div>
  );
}
