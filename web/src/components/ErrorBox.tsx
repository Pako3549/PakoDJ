"use client";

// Error screen used when the bot/API is reachable-behind-the-scenes or not
// yet configured: shows what happened without bouncing the user anywhere.
export default function ErrorBox({ message }: { message: string }) {
  return (
    <main className="max-w-7xl mx-auto px-4 py-6">
      <div className="bg-card border border-border rounded-xl p-6 max-w-md mx-auto text-center">
        <p className="text-sm text-red-400 mb-2">Web API unreachable</p>
        <p className="text-xs text-secondary mb-4">{message}</p>
        <p className="text-xs text-muted mb-4">
          Check the bot container (<code>docker compose ps</code>), then retry.
          If this is production, Cloudflare Tunnel status matters too.
        </p>
        <button
          onClick={() => window.location.reload()}
          className="px-4 py-2 rounded-lg bg-accent text-inverse text-sm font-medium hover:bg-accent/90 transition"
        >
          Retry
        </button>
      </div>
    </main>
  );
}
