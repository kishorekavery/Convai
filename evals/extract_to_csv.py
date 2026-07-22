import json
import csv
from pathlib import Path
from datetime import datetime

def generate_csv():
    # input_file = Path("evals/datasets/generated_golden_dataset.jsonl")
    input_file = Path("evals/datasets/qa_evaluated_dataset.jsonl")
    # output_file = Path(f"evals/datasets/test_data{datetime.now().strftime('%Y-%m-%d')}.csv")
    output_file = Path(f"evals/datasets/qa_evaluated_dataset{datetime.now().strftime('%Y-%m-%d')}.csv")
    
    if not input_file.exists():
        print(f"Error: {input_file} does not exist.")
        return

    with open(input_file, "r", encoding="utf-8") as f_in, \
         open(output_file, "w", newline="", encoding="utf-8") as f_out:
         
        writer = csv.writer(f_out)
        
        # Write headers matching the new QA dataset schema
        writer.writerow([
            "Case ID",
            "Timestamp",
            "Database Name",
            "User ID",
            "User Input",
            "Chat History Sent",
            "Knowledge Base Examples",
            "Generated SQL",
            "Assistant Response",
            "Latency (ms)",
            "Status",
            "Expected SQL",
            "Expected Response",
            "Label (Pass/Fail)",
            "Failure Category",
            "Reviewer Notes"
        ])
        
        # Parse JSONL and write rows
        for line in f_in:
            line = line.strip()
            if not line:
                continue
            
            data = json.loads(line)
            
            writer.writerow([
                data.get("case_id", ""),
                data.get("timestamp", ""),
                data.get("database_name", ""),
                data.get("user_id", ""),
                data.get("user_input", ""),
                data.get("chat_history_sent", ""),
                data.get("knowledge_base_examples", ""),
                data.get("generated_sql", ""),
                data.get("assistant_response", ""),
                data.get("latency_ms", ""),
                data.get("status", ""),
                data.get("expected_sql", ""),
                data.get("expected_response", ""),
                data.get("label", ""),
                data.get("failure_category", ""),
                data.get("reviewer_notes", "")
            ])
            
    print(f"Successfully generated {output_file} for human evaluation.")

if __name__ == "__main__":
    generate_csv()
