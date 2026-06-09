"""HTML text-mode prompt templates (MINDACT multi-choice QA format).

This is the AUXILIARY path — for comparison experiments with the
text-based approach from the Mind2Web paper.
"""

HTML_SYSTEM_PROMPT = """You are a helpful assistant that is great at website design, navigation, and executing tasks for the user."""


def build_html_user_prompt(
    task: str,
    cleaned_html: str,
    candidates: list[dict],
    history: list[str],
) -> str:
    """Build a multi-choice QA prompt in the MINDACT format.

    Args:
        task: Natural language task description.
        cleaned_html: Pruned HTML snippet.
        candidates: List of candidate elements with tags and text.
        history: Previous action descriptions.

    Returns:
        Multi-choice QA prompt string.
    """
    # Build choices
    choices = ["A. None of the above"]
    letters = "BCDEFGHIJKLMNOPQRSTUVWXYZ"
    for i, cand in enumerate(candidates[:10]):  # Show max 10 per group
        letter = letters[i] if i < len(letters) else f"Option{i}"
        choices.append(
            f"{letter}. [{cand.get('tag', '?')}] {cand.get('text', '')}"
        )

    history_str = "\n".join(history[-5:]) if history else "None"

    return f"""{cleaned_html}

Based on the HTML webpage above, try to complete the following task:
Task: {task}
Previous actions:
{history_str}

What should be the next action? Please select from the following choices
(If the correct action is not in the page above, please select A. 'None of the above'):

{chr(10).join(choices)}

Answer:"""
