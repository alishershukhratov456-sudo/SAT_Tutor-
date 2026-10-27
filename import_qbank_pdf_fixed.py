"""
Import SAT Reading & Writing and SAT Math questions from
College Board-style Question Bank PDF exports.

Place this script beside app.py.

Supported PDF locations:
    ./questionbank-export.pdf
    ./questionbank-export/*.pdf

Features:
- Supports Reading & Writing and Math.
- Preserves answer choices, correct answers, and explanations.
- Detects SAT domains and difficulty.
- Skips duplicate question text.
- Requests confirmation before writing to the database.
- Does not delete or overwrite existing questions.
- Rolls back the database transaction if the import fails.
"""

import re
import sys
from pathlib import Path

import pdfplumber

from app import app, db, SATQuestion


# --------------------------------------------------
# SAT DOMAINS
# --------------------------------------------------

DOMAINS = {
    "Reading & Writing": [
        "Information and Ideas",
        "Craft and Structure",
        "Expression of Ideas",
        "Standard English Conventions",
    ],
    "Math": [
        "Problem-Solving and Data Analysis",
        "Geometry and Trigonometry",
        "Advanced Math",
        "Algebra",
    ],
}


# --------------------------------------------------
# TEXT CLEANING
# --------------------------------------------------

def clean_text(value):
    value = value.replace("\u00a0", " ")
    value = value.replace("\u200b", "")

    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\s*\n\s*", "\n", value)

    return value.strip()


def normalized(value):
    return re.sub(r"\s+", " ", value).strip().casefold()


# --------------------------------------------------
# PDF EXTRACTION
# --------------------------------------------------

def extract_pdf_text(pdf_path):
    with pdfplumber.open(str(pdf_path)) as pdf:
        return "\n".join(
            page.extract_text(layout=False) or ""
            for page in pdf.pages
        )


# --------------------------------------------------
# REGEX HELPERS
# --------------------------------------------------

def find_heading(pattern, text):
    """
    Find a standalone heading, tolerating optional asterisks
    that might appear in extracted PDF text.
    """
    return re.search(
        rf"(?m)^[ \t]*\*?{pattern}\*?[ \t]*$",
        text,
        flags=re.I,
    )


def detect_metadata(metadata_text):
    """
    Identify the SAT subject, domain, and difficulty.

    The skill/subtopic between the domain and difficulty
    is permitted but is not stored separately.
    """
    subject_match = re.search(
        r"\bSAT\s+(Reading\s+and\s+Writing|Math)\b",
        metadata_text,
        flags=re.I,
    )

    if not subject_match:
        return None

    subject_label = subject_match.group(1).casefold()

    if subject_label == "math":
        subject = "Math"
    else:
        subject = "Reading & Writing"

    remaining = metadata_text[subject_match.end():].strip()

    difficulty_match = re.search(
        r"\b(Easy|Medium|Hard)\b\s*\*?\s*$",
        remaining,
        flags=re.I,
    )

    if not difficulty_match:
        return None

    difficulty = difficulty_match.group(1).title()
    domain_text = remaining[:difficulty_match.start()].strip()

    # Match the longest domain name first.
    domains = sorted(
        DOMAINS[subject],
        key=len,
        reverse=True,
    )

    topic = None

    for domain in domains:
        if re.match(
            rf"^{re.escape(domain)}\b",
            domain_text,
            flags=re.I,
        ):
            topic = domain
            break

    if not topic:
        return None

    return {
        "subject": subject,
        "topic": topic,
        "difficulty": difficulty,
    }


# --------------------------------------------------
# ANSWER CHOICE EXTRACTION
# --------------------------------------------------

def extract_options(options_text):
    """
    Extract A-D choices without changing the original
    question text or explanation.

    Supports both A. and A) style labels.
    """
    option_matches = list(
        re.finditer(
            r"(?m)^[ \t]*\*?([A-D])[.)][ \t]*",
            options_text,
            flags=re.I,
        )
    )

    options = {}

    for index, match in enumerate(option_matches):
        letter = match.group(1).upper()

        start = match.end()

        if index + 1 < len(option_matches):
            end = option_matches[index + 1].start()
        else:
            end = len(options_text)

        option_text = clean_text(options_text[start:end])

        if option_text:
            options[letter] = option_text

    return options


# --------------------------------------------------
# QUESTION PARSER
# --------------------------------------------------

def parse_records(full_text):
    # Split the PDF into individual question records.
    chunks = re.split(
        r"(?=Question ID:\s*\*?[0-9a-f]{8}\b)",
        full_text,
        flags=re.I,
    )

    records = []
    skipped_reasons = {}

    def skip(reason):
        skipped_reasons[reason] = (
            skipped_reasons.get(reason, 0) + 1
        )

    for chunk in chunks:
        if not re.search(
            r"Question ID:\s*\*?[0-9a-f]{8}\b",
            chunk,
            flags=re.I,
        ):
            continue

        question_heading = find_heading("Question", chunk)
        answer_heading = find_heading("Answer", chunk)
        rationale_heading = find_heading("Rationale", chunk)

        correct_match = re.search(
            r"Correct Answer:\s*\*?([A-D])\b",
            chunk,
            flags=re.I,
        )

        # Validate the structure before parsing.
        if not question_heading:
            skip("missing Question heading")
            continue

        if not answer_heading:
            skip("missing Answer heading")
            continue

        if answer_heading.start() <= question_heading.end():
            skip("Answer heading appears before Question")
            continue

        if not correct_match:
            skip("missing correct A-D answer")
            continue

        if not rationale_heading:
            skip("missing Rationale heading")
            continue

        if rationale_heading.start() <= correct_match.end():
            skip("Rationale appears before correct answer")
            continue

        # Detect subject, domain, and difficulty.
        metadata_text = re.sub(
            r"\s+",
            " ",
            chunk[:question_heading.start()],
        ).strip()

        metadata = detect_metadata(metadata_text)

        if not metadata:
            skip("subject/domain/difficulty not recognized")
            continue

        # Preserve the original Reading & Writing technique:
        # the question is before Answer; choices follow Answer.
        question_text = clean_text(
            chunk[
                question_heading.end():
                answer_heading.start()
            ]
        )

        options_text = chunk[
            answer_heading.end():
            correct_match.start()
        ].strip()

        options = extract_options(options_text)

        if not question_text:
            skip("empty question text")
            continue

        if any(
            letter not in options or not options[letter]
            for letter in "ABCD"
        ):
            skip("one or more A-D choices missing")
            continue

        # Extract the explanation following Rationale.
        explanation = clean_text(
            chunk[rationale_heading.end():]
        )

        if not explanation:
            skip("empty rationale")
            continue

        records.append({
            "subject": metadata["subject"],
            "topic": metadata["topic"],
            "difficulty": metadata["difficulty"],
            "question_text": question_text,
            "options": {
                letter: options[letter]
                for letter in "ABCD"
            },
            "correct_answer": correct_match.group(1).upper(),
            "explanation": explanation,
        })

    return records, skipped_reasons


# --------------------------------------------------
# FIND PDF FILES
# --------------------------------------------------

def find_pdf_files(folder):
    """
    Find either questionbank-export.pdf or PDFs inside
    the questionbank-export folder.
    """
    pdf_files = set(folder.glob("questionbank-export*.pdf"))

    export_folder = folder / "questionbank-export"

    if export_folder.is_dir():
        pdf_files.update(export_folder.glob("*.pdf"))

    return sorted(pdf_files)


# --------------------------------------------------
# DATABASE IMPORT
# --------------------------------------------------

def main():
    folder = Path(__file__).resolve().parent
    pdf_files = find_pdf_files(folder)

    if not pdf_files:
        print("ERROR: No matching Question Bank PDF found.")
        print("Expected one of:")
        print("  questionbank-export.pdf")
        print("  questionbank-export/*.pdf")
        sys.exit(1)

    if len(pdf_files) > 1:
        print("ERROR: Multiple matching PDFs found:")

        for path in pdf_files:
            print(f"  - {path.name}")

        print("Keep only the PDF you want to import, then retry.")
        sys.exit(1)

    pdf_path = pdf_files[0]
    print(f"Reading: {pdf_path.name}")

    try:
        full_text = extract_pdf_text(pdf_path)
    except Exception as exc:
        print(f"ERROR: Could not read PDF: {exc}")
        sys.exit(1)

    records, skipped_reasons = parse_records(full_text)

    print(f"\nParsed complete questions: {len(records)}")

    subject_counts = {}

    for record in records:
        subject = record["subject"]
        subject_counts[subject] = subject_counts.get(subject, 0) + 1

    for subject, count in sorted(subject_counts.items()):
        print(f"  {subject}: {count}")

    if skipped_reasons:
        print("\nRecords skipped during parsing:")

        for reason, count in sorted(skipped_reasons.items()):
            print(f"  - {reason}: {count}")

    if not records:
        print("\nERROR: No complete questions were parsed.")
        print("Nothing was imported. Your database is unchanged.")
        print("Inspect the PDF's extracted text and metadata format.")
        sys.exit(1)

    print("\nNo database changes have been made yet.")

    confirm = input(
        "Type YES to import these questions: "
    ).strip()

    if confirm != "YES":
        print("Cancelled. Your database was not changed.")
        return

    added = 0
    duplicates = 0

    with app.app_context():
        existing = {
            normalized(question_text)
            for (question_text,) in (
                SATQuestion.query
                .with_entities(SATQuestion.question_text)
                .all()
            )
        }

        try:
            for item in records:
                key = normalized(item["question_text"])

                if key in existing:
                    duplicates += 1
                    continue

                db.session.add(
                    SATQuestion(
                        subject=item["subject"],
                        topic=item["topic"],
                        difficulty=item["difficulty"],
                        question_text=item["question_text"],
                        options=item["options"],
                        correct_answer=item["correct_answer"],
                        explanation=item["explanation"],
                        source="manual",
                    )
                )

                existing.add(key)
                added += 1

            db.session.commit()

        except Exception:
            db.session.rollback()
            raise

    print("\nImport finished successfully.")
    print(f"New questions added: {added}")
    print(f"Duplicates skipped: {duplicates}")

    print("\nQuestions added by subject:")

    for subject in sorted(DOMAINS):
        count = sum(
            1
            for record in records
            if record["subject"] == subject
            and normalized(record["question_text"]) in existing
        )

        print(f"  {subject}: {count}")

    print("\nOpen your Question Bank and inspect several questions.")


if __name__ == "__main__":
    main()