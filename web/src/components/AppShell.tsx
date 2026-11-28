import { useState } from "react";
import { flushSync } from "react-dom";
import { Link, Navigate, Outlet, useLocation, useMatch } from "react-router-dom";
import {
  ActivityIcon,
  ChevronsUpDownIcon,
  FolderIcon,
  InboxIcon,
  MessageSquareIcon,
  PlusIcon,
  SettingsIcon,
  UsersIcon,
  type LucideIcon,
} from "lucide-react";
import { useTheme } from "next-themes";
import { toast } from "sonner";
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
import { captureScreen } from "@/lib/screenshot";
import { routeKey, usePageEnter } from "@/lib/pageEnter";
import FeedbackDialog from "@/components/FeedbackDialog";
import Logo from "@/components/Logo";

// Below lg the sidebar shows icons only; the label stays readable to screen readers.
const LABEL = "sr-only lg:not-sr-only";

function NavItem({ to, end, icon: Icon, children }: {
  to: string;
  end?: boolean;
  icon: LucideIcon;
  children: string;
}) {
  const active = useMatch({ path: to, end: Boolean(end) }) !== null;
  return (
    <Link
      to={to}
      title={children}
      aria-current={active ? "page" : undefined}
      data-active={active ? "true" : undefined}
      className={cn(
        "group relative flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors duration-150",
        "justify-center outline-none focus-visible:ring-[3px] focus-visible:ring-ring lg:justify-start",
        active
          ? "bg-accent text-accent-foreground"
          : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
      )}
    >
      {/* The gilt marker of the page a person is on; it grows in when the page becomes active. */}
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-1.5 left-0 w-[3px] rounded-full bg-brand transition-transform duration-[180ms] ease-out",
          active ? "scale-y-100" : "scale-y-0",
        )}
      />
      <Icon className="size-4 shrink-0 transition-transform duration-150 group-hover:translate-x-px" aria-hidden />
      <span className={LABEL}>{children}</span>
    </Link>
  );
}

export default function AppShell() {
  const me = useMe();
  const logout = useLogout();
  const { theme, setTheme } = useTheme();
  const location = useLocation();
  const pageKey = routeKey(location.pathname);
  const page = usePageEnter(pageKey);
  const [feedback, setFeedback] = useState<{ open: boolean; shot: Blob | null; page: string }>({
    open: false,
    shot: null,
    page: "",
  });

  /** Switch the theme as a cross-fade where the browser can do one, and at once elsewhere
   * and for a person who asked for less motion. */
  function switchTheme(value: string) {
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (still || typeof document.startViewTransition !== "function") {
      setTheme(value);
      return;
    }
    document.startViewTransition(() => flushSync(() => setTheme(value)));
  }

  async function openFeedback() {
    const page = location.pathname + location.search;
    const busy = toast.loading("Taking a screenshot…");
    const shot = await captureScreen();
    toast.dismiss(busy);
    setFeedback({ open: true, shot, page });
  }

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
      <aside className="sticky top-0 flex h-screen w-16 shrink-0 flex-col gap-4 border-r bg-muted/40 px-2 py-4 lg:w-56 lg:px-3">
        <Link
          to="/"
          title="Jerome"
          className="flex justify-center rounded-md py-1 outline-none focus-visible:ring-[3px] focus-visible:ring-ring lg:justify-start lg:px-3"
        >
          <Logo wordmarkClassName={LABEL} />
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
          {admin && <NavItem to="/feedback" icon={InboxIcon}>Feedback</NavItem>}
        </nav>
        <div className="mt-auto flex flex-col gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-center text-muted-foreground lg:justify-start"
            title="Send feedback"
            onClick={() => void openFeedback()}
          >
            <MessageSquareIcon aria-hidden />
            <span className={LABEL}>Send feedback</span>
          </Button>
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
              <DropdownMenuRadioGroup value={theme ?? "system"} onValueChange={switchTheme}>
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
      {/* One element for every page: a new page replays its enter animation here, and
          nothing below is remounted. */}
      <div ref={page} data-page data-route-key={pageKey} className="page-enter min-w-0 flex-1">
        <Outlet />
      </div>
      <FeedbackDialog
        open={feedback.open}
        onOpenChange={(open) => setFeedback((f) => ({ ...f, open }))}
        screenshot={feedback.shot}
        page={feedback.page}
      />
    </div>
  );
}
