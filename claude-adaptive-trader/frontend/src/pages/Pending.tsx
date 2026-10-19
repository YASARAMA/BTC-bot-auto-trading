import { Hourglass } from "lucide-react";
import { EmptyState } from "../components/ui/Feedback";

// Shown only while a page's backend is being built in a later stage of this project.
export function Pending({ name }: { name: string }) {
  return (
    <EmptyState title={`${name} is being connected to the backend`} icon={<Hourglass className="size-6" />}>
      This page is wired to real backend state in a later build stage. It shows no placeholder numbers in the meantime.
    </EmptyState>
  );
}
