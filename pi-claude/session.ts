// A pi extension, for pi run on another host as an agent (Baude's agentproxy): pi's session is the chat's host's, so
// each entry pi adds to it is sent there as it's added, through pi-claude, as a notice: {"baude": "pi_session",
// "header", "from", "entries"}, the entries from index from on (those before it sent already). pi's session is
// append-only, so from is where what was sent stops matching: the count sent, unless pi's rewritten it (all of it,
// then, from 0). The first a run sends is all of it (what pi took up, and what it's added in memory before it has a
// file). pi's own file, here, is only a copy it reads from.
export default function (pi: any) {
	let sent: string[] = [];

	const send = (ctx: any) => {
		const sm = ctx.sessionManager;
		const header = sm.getHeader();
		const entries = sm.getEntries();
		if (!header) return;
		let from = 0;
		while (from < sent.length && from < entries.length && entries[from].id === sent[from]) from++;
		if (from === entries.length && from === sent.length) return;
		ctx.ui.notify(JSON.stringify({ baude: "pi_session", header, from, entries: entries.slice(from) }), "info");
		sent = entries.map((e: any) => e.id);
	};

	for (const name of ["message_end", "turn_end", "agent_end", "session_compact", "session_tree", "session_shutdown"]) {
		pi.on(name, async (_event: any, ctx: any) => send(ctx));
	}
}
