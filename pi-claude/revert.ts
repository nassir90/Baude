// A pi extension: what lets Baude revert a message of yours in a pi chat, as claude's rewind_conversation does.
// pi-claude says each message's uuid (Baude's) with /baude-uuid just before it sends the message; the uuid is kept on
// the message as pi saves it (baudeUuid: a field the model's API never sees). /baude-rewind {"uuid", "index"} moves
// pi's session back to before that message (navigateTree: the session file is only appended to, the dropped turns
// left on a branch nobody follows), so what the model sees from then on is what comes before it. A message saved
// before this extension (no baudeUuid) is found by index instead: which of the branch's messages of yours it is.
// It answers with a notice, {"baude": "rewound", "uuid", "ok", "why"}.
export default function (pi: any) {
	let waiting: string[] = [];

	pi.registerCommand("baude-uuid", {
		description: "Baude: the uuid of the message about to be sent",
		handler: async (args: string) => {
			if (args.trim()) waiting.push(args.trim());
		},
	});

	pi.on("message_end", async (event: any) => {
		const m = event.message;
		if (m?.role !== "user" || m.baudeUuid || !waiting.length) return undefined;
		return { message: { ...m, baudeUuid: waiting.shift() } };
	});

	pi.registerCommand("baude-rewind", {
		description: "Baude: the session moved back to before a message of yours",
		handler: async (args: string, ctx: any) => {
			let ask: any = {};
			try {
				ask = JSON.parse(args);
			} catch {}
			const answer = (ok: boolean, why?: string) =>
				ctx.ui.notify(JSON.stringify({ baude: "rewound", uuid: ask.uuid, ok, why }), "info");
			waiting = []; // (messages still to come: sent after it, gone with it)
			try {
				if (!ctx.isIdle()) {
					ctx.abort();
					await ctx.waitForIdle();
				}
				const mine = ctx.sessionManager
					.getBranch()
					.filter((e: any) => e.type === "message" && e.message?.role === "user");
				let target = mine.find((e: any) => ask.uuid && e.message.baudeUuid === ask.uuid);
				if (!target && Number.isInteger(ask.index) && ask.index >= 0 && ask.index < mine.length
					&& !mine[ask.index].message.baudeUuid) target = mine[ask.index];
				if (!target) return answer(false, "pi has no record of that message");
				const r = await ctx.navigateTree(target.id, { summarize: false });
				if (r?.cancelled) return answer(false, "pi wouldn't go back");
				pi.appendEntry("baude-revert", { uuid: ask.uuid }); // (the new place kept: pi resumes at its last entry)
				answer(true);
			} catch (e: any) {
				answer(false, String(e?.message || e));
			}
		},
	});
}
