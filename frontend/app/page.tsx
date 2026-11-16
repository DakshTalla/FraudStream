import Dashboard from "@/components/Dashboard";

// The page itself is a server component; everything live happens inside the
// <Dashboard> client component, which owns the WebSocket connection.
export default function Home() {
  return <Dashboard />;
}
