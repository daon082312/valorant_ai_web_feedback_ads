from __future__ import annotations

import re

from app_v6_10 import App as V610App
from main import ACCENT, GOOD, MUTED


_SCORE_RE = re.compile(r"(?<!\d)(?:100|\d{1,2})(?:\.\d+)?/100(?!\d)")


class App(V610App):
    """v6.11: cleaner toolbar and higher-contrast post-fight feedback."""

    def _build_ui(self) -> None:
        super()._build_ui()

        # Reference-profile learning is intentionally removed from the visible UI.
        try:
            if hasattr(self, "round_reference_btn") and self.round_reference_btn.winfo_exists():
                self.round_reference_btn.pack_forget()
        except Exception:
            pass

        self._style_feedback_panel()

    def _style_feedback_panel(self) -> None:
        try:
            if not hasattr(self, "feedback") or not self.feedback.winfo_exists():
                return
            family = "Malgun Gothic"
            self.feedback.configure(
                font=(family, 11),
                spacing1=3,
                spacing2=1,
                spacing3=7,
            )
            self.feedback.tag_configure("heading", foreground=ACCENT, font=(family, 12, "bold"))
            self.feedback.tag_configure("good", foreground=GOOD, font=(family, 11, "bold"))
            self.feedback.tag_configure("muted", foreground=MUTED, font=(family, 10))
            self.feedback.tag_configure("score", foreground=ACCENT, font=(family, 13, "bold"))
        except Exception:
            pass

    def _append_feedback(self, text: str, tag: str | None = None) -> None:
        """Append feedback and automatically emphasize /100 scores in red."""
        if not hasattr(self, "feedback"):
            return
        try:
            self.feedback.configure(state="normal")
            start = self.feedback.index("end-1c")
            if tag:
                self.feedback.insert("end", text, tag)
            else:
                self.feedback.insert("end", text)

            # Tags are configured lazily too because this override is called while
            # parent constructors are still building the UI.
            family = "Malgun Gothic"
            self.feedback.tag_configure("score", foreground=ACCENT, font=(family, 13, "bold"))
            for match in _SCORE_RE.finditer(text):
                score_start = f"{start}+{match.start()}c"
                score_end = f"{start}+{match.end()}c"
                self.feedback.tag_add("score", score_start, score_end)

            self.feedback.see("end")
        finally:
            try:
                self.feedback.configure(state="disabled")
            except Exception:
                pass


if __name__ == "__main__":
    App().mainloop()
