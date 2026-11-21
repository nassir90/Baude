"""The feedback of the screenshots and the parity rig, as a fake GitHub's state (tests/fake_gh.py's): issues and pull
requests as ☰ → Ongoing Feedback shows them. No GTK here."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fixes  # noqa: E402


def ago_iso(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))


def issue(number, title, body, age, state="OPEN", reason="", comments=()):
    return {"number": number, "title": title, "body": body, "state": state, "stateReason": reason,
            "author": {"login": "nassir90"}, "createdAt": ago_iso(age), "updatedAt": ago_iso(age / 3),
            "url": f"https://github.com/nassir90/Baude/issues/{number}", "comments": list(comments)}


def fix_comment(text="", age=600, **mark):
    return {"author": {"login": "nassir90"}, "authorAssociation": "OWNER", "createdAt": ago_iso(age),
            "body": fixes.round_body("nassir90/Baude", text, **mark)}


def state(viewer="ADMIN", private=False):
    """The fake GitHub: feedback in each state (#12 ready for review, with its images; #11 being worked on; #10 and #9
    open; #8 merged; #7 rejected). private: a private repository for feedback has one ready too."""
    pr = {"number": 41, "title": "Give the input box a stronger outline", "state": "OPEN", "isDraft": False,
          "headRefName": "fix/12", "url": "https://github.com/nassir90/Baude/pull/41", "mergedAt": None,
          "updatedAt": ago_iso(600), "closingIssuesReferences": [{"number": 12}]}
    merged = {**pr, "number": 38, "state": "MERGED", "headRefName": "fix/8", "mergedAt": ago_iso(86400),
              "closingIssuesReferences": [{"number": 8}], "url": "https://github.com/nassir90/Baude/pull/38"}
    out = {"viewer": viewer, "prs": [pr, merged], "issues": [
        issue(12, "The input box is hard to find when the chat is empty",
              "In an empty chat, the input box barely stands out from the window around it.\n\n"
              "Could it have a stronger outline?\n\n_Sent from Baude's Send Feedback…_", 7200, comments=[
                  fix_comment(age=1500, state="working", round=1, machine="workshop"),
                  fix_comment("The input box now has a 2px outline in the accent colour (`.composer-box` in "
                              "`widgets.py`), in both themes.", age=600, state="ready", round=1, machine="workshop",
                              shots=[{"label": "The empty chat", "before": "12/1/1-before.png",
                                      "after": "12/1/1-after.png"}])]),
        issue(11, "Kubernetes view forgets the namespace when it reloads",
              "After a cluster reconnects, the namespace goes back to *default*.", 18000, comments=[
                  fix_comment(age=240, state="working", round=1, machine="mac-studio")]),
        issue(10, "Zoom is jittery: passes through two sizes on resize",
              "Pressing ⌘+ on a long chat, the text jumps to one size and then settles on another.\n\n"
              "## Context\n\n- folder: ~/projects/Baude\n\n_Sent from Baude's Send Feedback…_", 90000),
        issue(9, "Show which model answered each message", "A small label beside each reply would do.", 172800),
        issue(8, "Highlight a hovered git reference in the Git pane", "As the editor does.", 400000, "CLOSED", "COMPLETED"),
        issue(7, "Replace the sidebar with a floating palette", "It would save room.", 600000, "CLOSED", "NOT_PLANNED",
              comments=[{"author": {"login": "nassir90"}, "authorAssociation": "OWNER", "createdAt": ago_iso(500000),
                         "body": "Rejected from Baude."}]),
    ]}
    if private:
        inside = {**issue(3, "The approval card's border is thicker than the tool rows'",
                          "Approvals have a 2px border, failed and passed tools 1px.", 5400, comments=[
                              fix_comment("Every card's border is now 1px (`.perm-card` in `widgets.py`).", age=300,
                                          state="ready", round=1, machine="workshop")]),
                  "url": "https://github.com/nassir90/BaudeInternal/issues/3"}
        out["issues_on"] = {"nassir90/BaudeInternal": [inside]}
        out["prs"].append({**pr, "number": 44, "title": "One border width for every card",
                           "headRefName": "fix/BaudeInternal-3", "url": "https://github.com/nassir90/Baude/pull/44",
                           "updatedAt": ago_iso(300), "closingIssuesReferences": []})
    return out
