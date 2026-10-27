"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ThemeToggle } from "@/components/ThemeToggle";

const links = [
  { href: "/", label: "下载" },
  { href: "/queue", label: "任务队列" },
  { href: "/subscriptions", label: "订阅管理" },
  { href: "/profiles", label: "博主图库" },
  { href: "/downloads", label: "下载文件" },
  { href: "/settings", label: "设置" },
];

export function Navbar() {
  const pathname = usePathname();
  return (
    <div className="navbar bg-base-100 border-b border-base-300 sticky top-0 z-10">
      <nav aria-label="主导航" className="container mx-auto max-w-3xl flex flex-1 flex-wrap items-center">
        <div className="flex-1">
          <Link href="/" className="btn btn-ghost text-lg font-bold">
            📸 gallery-dl-web
          </Link>
        </div>
        <div className="flex-none flex items-center gap-1">
          <ul className="menu menu-horizontal gap-1">
            {links.map((l) => (
              <li key={l.href}>
                <Link
                  href={l.href}
                  aria-current={pathname === l.href ? "page" : undefined}
                  className={pathname === l.href ? "active font-semibold" : ""}
                >
                  {l.label}
                </Link>
              </li>
            ))}
          </ul>
          <ThemeToggle />
        </div>
      </nav>
    </div>
  );
}
