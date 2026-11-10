import { redirect } from "next/navigation";

/** The home page is today's performance. */
export default function Home() {
  redirect("/performance");
}
