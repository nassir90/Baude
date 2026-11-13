"""baudelib: what Baude knows of Claude Code, without GTK, for Baude and for programs with no window.

- claude: the claude binary, and a headless claude speaking stream-json (ClaudeProc)
- transcripts: Claude Code's transcripts, and the conversation each records
- wire: the messages Baude and a server say to each other, a JSON object a line
- server: the host process: Claude Code sessions started and steered on its socket, here or over ssh
- client: a server as Baude sees it: its sessions, a transcript mirrored, a session steered (RemoteProc)
- selfupdate: kept up to date from git (Baude's and Record's updater's, and the host process's own)
"""
