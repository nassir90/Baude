"""baudelib: what Baude knows of Claude Code, without GTK, for Baude and for programs with no window.

- claude: the claude binary, and a headless claude speaking stream-json (ClaudeProc)
- transcripts: Claude Code's transcripts, and the conversation each records
- wire: the messages Baude and a server say to each other, a JSON object a line
- discovery: servers found on the local network (DNS-SD over multicast DNS, _baude._tcp)
- server: the headless server: Claude Code sessions started and steered over the network
- client: a server as Baude sees it: its sessions, a transcript mirrored, a session steered (RemoteProc)
"""
