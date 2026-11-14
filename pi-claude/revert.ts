// A pi extension: what lets Baude revert a message of yours in a pi chat, as claude's rewind_conversation does.
// pi-claude says each message's uuid (Baude's) and text with /baude-uuid, and sends the message once that's taken; the
// uuid is kept on the user message of that text as pi saves it (baudeUuid: a field the model's API never sees; one
// pi never took is passed over by the next that's saved). /baude-rewind {"uuid", "index"} moves
// pi's session back to before that message (navigateTree: the session file is only appended to, the dropped turns
// left on a branch nobody follows), so what the model sees from then on is what comes before it. A message saved
// before this extension (no baudeUuid) is found by index instead (which of the branch's messages of yours it is), only
// while the branch has as many as Baude's record (count): else they don't line up, and it's refused.
// It answers with a notice, {"baude": "rewound", "uuid", "ok", "why"}.
export default function (pi: any) {
	let waiting: { uuid: string; text: string }[] = [];
	const text = (m: any) =>
		typeof m.content === "string" ? m.content
			: (m.content || []).filter((c: any) => c?.type === "text").map((c: any) => c.text).join("\n");

	pi.registerCommand("baude-uuid", {
		description: "Baude: the uuid of the message about to be sent",
		handler: async (args: string) => {
			try {
				const said = JSON.parse(args);
				if (said?.uuid) waiting.push({ uuid: said.uuid, text: said.text ?? "" });
			} catch {}
		},
	});

	pi.on("message_end", async (event: any) => {
		const m = event.message;
		if (m?.role !== "user" || m.baudeUuid) return undefined;
		const at = waiting.findIndex((w) => w.text === text(m));
		if (at < 0) return undefined;
		const uuid = waiting[at].uuid;
		waiting = waiting.slice(at + 1);
		return { message: { ...m, baudeUuid: uuid } };
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
				if (!target && Number.isInteger(ask.index) && ask.index >= 0 && ask.count === mine.length
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
