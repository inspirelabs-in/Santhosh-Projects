import { RoleGate } from "@/components/role-gate";

export default function CEOLayout({ children }: { children: React.ReactNode }) {
  return <RoleGate allow={["admin"]}>{children}</RoleGate>;
}
