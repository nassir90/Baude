// A pi extension: puts each tool call to Baude, through pi-claude, before it runs. pi-claude answers the input dialog
// with {"behavior": "allow", "input": ...} or {"behavior": "deny", "message": ...}.
export default function (pi: any) {
	pi.on("tool_call", async (event: any, ctx: any) => {
		const question = JSON.stringify({ baude: "can_use_tool", id: event.toolCallId, tool: event.toolName, input: event.input });
		let reply: any = {};
		try {
			reply = JSON.parse((await ctx.ui.input(question, "")) ?? "{}");
		} catch {}
		if (reply.behavior === "allow") {
			if (reply.input) Object.assign(event.input, reply.input);
			return undefined;
		}
		return { block: true, reason: reply.message || "The user said no to this." };
	});
}
