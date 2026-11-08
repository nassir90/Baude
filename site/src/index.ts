// Baude's page on mpts.ie. Everything is a static asset; requests reach the Worker only when no asset matches.
export default {
	async fetch(request: Request) {
		const url = new URL(request.url);
		if (url.pathname === "/apps/baude") return Response.redirect(`${url.origin}/apps/baude/${url.search}`, 301);
		return new Response("Not found", { status: 404 });
	},
};
