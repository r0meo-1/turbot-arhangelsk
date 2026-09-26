import json
import os
import re
from pathlib import Path

from .logic import utcnow


START = "<!-- WORKFLOW-AUTO:BEGIN -->"
END = "<!-- WORKFLOW-AUTO:END -->"


class MarkdownProjection:
    def __init__(self, path: str):
        self.path = Path(path)

    def render(self, tasks, reviews):
        lines = [
            "# Automated workflow state",
            "",
            f"_Generated: {utcnow().isoformat()}_",
            "",
        ]

        for priority in (
            "critical",
            "high",
            "normal",
            "low",
        ):
            lines += [
                f"## {priority.title()}",
                "",
            ]

            matching = [
                x for x in tasks
                if x["priority"] == priority
            ]

            if not matching:
                lines.append("_No tasks._")
            else:
                for task in matching:
                    due = (
                        f" · due `{task['due_date']}`"
                        if task["due_date"]
                        else ""
                    )

                    lines.append(
                        f"- [ ] {task['title']}"
                        f"{due}"
                        f" · `{task['project']}`"
                        f" <!-- task:{task['id']} -->"
                    )

            lines.append("")

        lines += [
            "## Needs review",
            "",
        ]

        if not reviews:
            lines.append("_Nothing waiting for review._")

        for review in reviews:
            data = json.loads(
                review["candidate_json"]
            )

            lines.append(
                f"- {data['title']} "
                f"— **{review['reason']}**"
            )

        return "\n".join(lines).rstrip() + "\n"

    def write(self, generated: str):
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        existing = (
            self.path.read_text(encoding="utf-8")
            if self.path.exists()
            else "# Project Board\n\n"
        )

        block = (
            f"{START}\n"
            f"{generated}"
            f"{END}"
        )

        pattern = re.compile(
            re.escape(START)
            + r".*?"
            + re.escape(END),
            re.S,
        )

        if pattern.search(existing):
            output = pattern.sub(
                lambda _: block,
                existing,
            )
        else:
            output = (
                existing.rstrip()
                + "\n\n"
                + block
                + "\n"
            )

        tmp = self.path.with_suffix(
            self.path.suffix + ".tmp"
        )

        tmp.write_text(
            output,
            encoding="utf-8",
        )

        os.replace(tmp, self.path)
