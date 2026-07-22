import json

from config import get_logger, JUDGE_MODEL_ID
from models import ChatModel
from prompts import format_groundedness_judge_prompt

logging = get_logger(__name__)

_VALID_LABELS = {"grounded", "partially_grounded", "hallucinated"}


def _clean_json_output(output: str) -> str:
    """Strip an optional ```json fence the judge model sometimes wraps its output in."""
    output = output.strip()
    if output.startswith("```") and output.endswith("```"):
        lines = output.splitlines()
        return "\n".join(lines[1:-1])
    return output


class GroundednessJudge:
    """
    LLM-as-judge for answer groundedness/faithfulness: verifies that a
    generated answer states only facts present in a given data context.

    Uses JUDGE_MODEL_ID (config/settings.py) - recommended to be a different
    model family than CHAT_MODEL_ID/SQL_MODEL_ID to avoid self-grading bias.
    """

    def __init__(self, model_id: str = JUDGE_MODEL_ID):
        self.model = ChatModel(model_id=model_id)

    def judge(self, user_input: str, context: str, answer: str) -> dict:
        """
        Args:
            user_input: the user's original question.
            context: the data the answer must be grounded in (e.g. the
                tabulated SQL result rows / "metadata.grounding_context").
            answer: the AI-generated answer to verify.
        Returns:
            dict with keys: claims, label, unsupported_claims, rationale.
        Raises:
            RuntimeError / json.JSONDecodeError / ValueError if the judge
            model fails to produce a parseable, well-formed verdict.
        """

        prompt = format_groundedness_judge_prompt(user_input, context, answer)
        raw_output = self.model.generate_response(prompt)

        if not raw_output or not raw_output.strip():
            raise RuntimeError("Empty response from judge model.")

        try:
            result = json.loads(_clean_json_output(raw_output))
        except json.JSONDecodeError as e:
            logging.error(
                "Judge output was not valid JSON: %s\nRaw output: %s", e, raw_output
            )
            raise

        label = result.get("label")
        if label not in _VALID_LABELS:
            raise ValueError(f"Judge returned an unexpected label: {label!r}")

        result.setdefault("claims", [])
        result.setdefault("unsupported_claims", [])
        result.setdefault("rationale", "")

        return result


class QAJudge:
    """
    LLM-as-a-QA judge for evaluating generated datasets.
    """

    def __init__(self, model_id: str = JUDGE_MODEL_ID):
        self.model = ChatModel(model_id=model_id)

    def evaluate_case(self, user_input: str, knowledge_base_examples: str, generated_sql: str, assistant_response: str) -> dict:
        from prompts.prompts_templates import format_qa_judge_prompt
        prompt = format_qa_judge_prompt(user_input, knowledge_base_examples, generated_sql, assistant_response)
        raw_output = self.model.generate_response(prompt)

        if not raw_output or not raw_output.strip():
            raise RuntimeError("Empty response from QA judge model.")

        try:
            result = json.loads(_clean_json_output(raw_output), strict=False)
        except json.JSONDecodeError as e:
            logging.error(
                "QA Judge output was not valid JSON: %s\nRaw output: %s", e, raw_output
            )
            raise

        return {
            "expected_sql": result.get("expected_sql", ""),
            "expected_response": result.get("expected_response", ""),
            "label": result.get("label", ""),
            "failure_category": result.get("failure_category", ""),
            "reviewer_notes": result.get("reviewer_notes", "")
        }

if __name__ == "__main__":
    import sys
    from pathlib import Path

    if "--run-qa" in sys.argv:
        judge = QAJudge()
        input_file = Path("evals/datasets/generated_golden_dataset.jsonl")
        output_file = Path("evals/datasets/qa_evaluated_dataset.jsonl")

        if not input_file.exists():
            print(f"File not found: {input_file}")
            sys.exit(1)

        print(f"Running QA Judge on {input_file}...")
        
        evaluated_cases = []
        with open(input_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                case = json.loads(line)
                
                if not case.get("label"):  # Only judge if unlabelled
                    print(f"Evaluating case {case.get('case_id')}...")
                    verdict = judge.evaluate_case(
                        user_input=case.get("user_input", ""),
                        knowledge_base_examples=case.get("knowledge_base_examples", ""),
                        generated_sql=case.get("generated_sql", ""),
                        assistant_response=case.get("assistant_response", "")
                    )
                    
                    case.update(verdict)
                
                evaluated_cases.append(case)

        with open(output_file, "w", encoding="utf-8") as f:
            for case in evaluated_cases:
                f.write(json.dumps(case) + "\n")
                
        print(f"Finished evaluating. Output saved to {output_file}")
        sys.exit(0)

    judge = GroundednessJudge()

    grounded_result = judge.judge(
        user_input="How many work orders are open?",
        context="count\n-----\n42",
        answer="There are 42 open work orders.",
    )
    print("Expected label=grounded ->", grounded_result)

    hallucinated_result = judge.judge(
        user_input="How many work orders are open?",
        context="count\n-----\n42",
        answer="There are 42 open work orders, mostly created by John Smith in the Chennai plant.",
    )
    print("Expected label=partially_grounded/hallucinated ->", hallucinated_result)


