

import os

import re

import uuid



from PIL import Image, UnidentifiedImageError



from dotenv import load_dotenv

from flask import (

    Flask, jsonify, render_template, request,

    redirect, url_for, flash

)

from flask_login import (

    LoginManager, UserMixin, login_user,

    login_required, logout_user, current_user

)

from flask_session import Session

from flask_sqlalchemy import SQLAlchemy

from flask_wtf.csrf import CSRFProtect, CSRFError

from werkzeug.exceptions import RequestEntityTooLarge

from werkzeug.security import generate_password_hash, check_password_hash

from google import genai

from google.genai import types



load_dotenv()



app = Flask(__name__)



secret_key = os.getenv("FLASK_SECRET_KEY")

if not secret_key:

    raise RuntimeError(

        "FLASK_SECRET_KEY is missing. Add it to your local .env file."

    )



def _database_url() -> str:
    """
    Return the PostgreSQL connection URL.

    Railway injects DATABASE_URL automatically when a Postgres service is
    linked.  SQLAlchemy 2.x requires the 'postgresql+psycopg2://' scheme, so
    we normalise the older 'postgres://' prefix that Railway sometimes sets.
    """
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is missing. "
            "Set it in your Railway environment or local .env file."
        )
    # Heroku/Railway may still emit the legacy prefix.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


# Use SQLAlchemy-based server-side sessions so that session data is stored in
# Postgres instead of the local filesystem (Railway has an ephemeral FS).
_is_production = os.getenv("RAILWAY_ENVIRONMENT") is not None

app.config.update(
    SECRET_KEY=secret_key,
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,

    # PostgreSQL — URL injected by Railway or set in .env for local dev.
    SQLALCHEMY_DATABASE_URI=_database_url(),
    SQLALCHEMY_ENGINE_OPTIONS={
        # Keep connections alive across requests on Railway's managed Postgres.
        "pool_pre_ping": True,
        "pool_recycle": 300,
    },

    # Store sessions in the database, not on the filesystem.
    SESSION_TYPE="sqlalchemy",
    SESSION_PERMANENT=False,
    SESSION_USE_SIGNER=True,

    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Secure cookies in production (Railway serves over HTTPS).
    SESSION_COOKIE_SECURE=_is_production,
)



db = SQLAlchemy(app)

# Pass the SQLAlchemy instance so Flask-Session can use the database backend.
app.config["SESSION_SQLALCHEMY"] = db

server_session = Session(app)

csrf = CSRFProtect(app)



login_manager = LoginManager(app)

login_manager.login_view = "login"

login_manager.login_message = "Please sign in to access SAT Tutor."







class User(UserMixin, db.Model):

    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(db.String(30), unique=True, nullable=False)

    email = db.Column(db.String(254), unique=True, nullable=False)

    password_hash = db.Column(db.String(255), nullable=False)



    # New profile and permissions fields

    avatar_filename = db.Column(db.String(255), nullable=True)

    is_admin = db.Column(db.Boolean, nullable=False, default=False)







# SAT Bank database models
class SATQuestion(db.Model):
    __tablename__ = "sat_questions"

    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(30), nullable=False)
    topic = db.Column(db.String(100), nullable=False)
    difficulty = db.Column(db.String(20), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    options = db.Column(db.JSON, nullable=False)
    correct_answer = db.Column(db.String(1), nullable=False)
    explanation = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(20), nullable=False, default="manual")
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=db.func.current_timestamp()
    )


class SavedQuestion(db.Model):
    __tablename__ = "saved_questions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    question_id = db.Column(
        db.Integer, db.ForeignKey("sat_questions.id"), nullable=False
    )
    saved_at = db.Column(
        db.DateTime, nullable=False, default=db.func.current_timestamp()
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "question_id", name="unique_saved_question"
        ),
    )


class PracticeAttempt(db.Model):
    __tablename__ = "practice_attempts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    question_id = db.Column(
        db.Integer, db.ForeignKey("sat_questions.id"), nullable=False
    )
    selected_answer = db.Column(db.String(1), nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False)
    attempted_at = db.Column(
        db.DateTime, nullable=False, default=db.func.current_timestamp()
    )


@login_manager.user_loader

def load_user(user_id):

    try:

        return db.session.get(User, int(user_id))

    except (TypeError, ValueError):

        return None





@app.errorhandler(CSRFError)

def handle_csrf_error(error):

    if request.path == "/ask":

        return jsonify({

            "error": "Your session security token expired. Refresh the page and try again."

        }), 400



    return (

        "Your form security token expired or was invalid. Please go back, refresh, and try again.",

        400,

    )





@login_manager.unauthorized_handler

def unauthorized():

    if request.path == "/ask":

        return jsonify({

            "error": "Please sign in before asking your SAT tutor a question."

        }), 401



    return redirect(url_for("login"))





@app.route("/signup", methods=["GET", "POST"])

def signup():

    if current_user.is_authenticated:

        return redirect(url_for("home"))



    if request.method == "POST":

        username = request.form.get("username", "").strip()

        email = request.form.get("email", "").strip().lower()

        password = request.form.get("password", "")



        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", username):

            flash(

                "Username must be 3–30 characters and use only letters, numbers, "

                "dots, underscores, or hyphens."

            )

            return render_template("signup.html"), 400



        if (

            len(email) > 254

            or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)

        ):

            flash("Please enter a valid email address.")

            return render_template("signup.html"), 400



        if len(password) < 8 or len(password) > 128:

            flash("Password must be between 8 and 128 characters.")

            return render_template("signup.html"), 400



        existing_user = db.session.execute(

            db.select(User).where(

                db.or_(

                    db.func.lower(User.username) == username.lower(),

                    db.func.lower(User.email) == email,

                )

            )

        ).scalar_one_or_none()



        if existing_user:

            flash("That username or email is already registered.")

            return render_template("signup.html"), 409



        user = User(

            username=username,

            email=email,

            password_hash=generate_password_hash(password),

        )



        try:

            db.session.add(user)

            db.session.commit()

        except Exception:

            db.session.rollback()

            app.logger.exception("Account creation failed")

            flash("We couldn't create your account. Please try again.")

            return render_template("signup.html"), 500



        login_user(user, fresh=True)

        flash("Your account was created successfully. Welcome to SAT Tutor!")

        return redirect(url_for("home"))



    return render_template("signup.html")





@app.route("/login", methods=["GET", "POST"])

def login():

    if current_user.is_authenticated:

        return redirect(url_for("home"))



    if request.method == "POST":

        identity = request.form.get("identity", "").strip()

        password = request.form.get("password", "")



        user = db.session.execute(

            db.select(User).where(

                db.or_(

                    db.func.lower(User.username) == identity.lower(),

                    db.func.lower(User.email) == identity.lower(),

                )

            )

        ).scalar_one_or_none()



        if user is None or not check_password_hash(

            user.password_hash, password

        ):

            flash("Incorrect username/email or password.")

            return render_template("login.html"), 401



        login_user(user, fresh=True)

        return redirect(url_for("home"))



    return render_template("login.html")





@app.route("/logout", methods=["POST"])

@login_required

def logout():

    logout_user()

    flash("You've been signed out.")

    return redirect(url_for("login"))





@app.route("/")

@login_required

def home():

    return render_template("index.html")


# =========================
# SAT QUESTION BANK (QBank)
# =========================

@app.route("/qbank")
@login_required
def qbank():
    return render_template("qbank.html")


@app.route("/api/qbank/questions", methods=["GET"])
@login_required
def qbank_questions():
    subject = request.args.get("subject", "").strip()
    topic = request.args.get("topic", "").strip()
    difficulty = request.args.get("difficulty", "").strip()
    search = request.args.get("search", "").strip()
    saved_only = request.args.get("saved", "").lower() == "true"

    query = db.select(SATQuestion)

    if subject and subject != "All":
        query = query.where(SATQuestion.subject == subject)

    if topic and topic != "All":
        query = query.where(SATQuestion.topic == topic)

    if difficulty and difficulty != "All":
        query = query.where(SATQuestion.difficulty == difficulty)

    if search:
        query = query.where(
            db.or_(
                SATQuestion.question_text.ilike(f"%{search}%"),
                SATQuestion.topic.ilike(f"%{search}%")
            )
        )

    if saved_only:
        query = query.join(
            SavedQuestion,
            SavedQuestion.question_id == SATQuestion.id
        ).where(SavedQuestion.user_id == current_user.id)

    questions = db.session.execute(
        query.order_by(SATQuestion.created_at.desc())
    ).scalars().all()

    return jsonify([
        {
            "id": q.id,
            "subject": q.subject,
            "topic": q.topic,
            "difficulty": q.difficulty,
            "question": q.question_text,
            "options": q.options,
            "source": q.source,
            "saved": db.session.execute(
                db.select(SavedQuestion.id).where(
                    SavedQuestion.user_id == current_user.id,
                    SavedQuestion.question_id == q.id
                )
            ).scalar_one_or_none() is not None
        }
        for q in questions
    ])


@app.route("/api/qbank/questions/<int:question_id>/answer",
           methods=["POST"])
@login_required
def qbank_check_answer(question_id):
    question = db.session.get(SATQuestion, question_id)

    if question is None:
        return jsonify({"error": "Question not found."}), 404

    data = request.get_json(silent=True) or {}
    selected = str(data.get("answer", "")).strip().upper()

    valid_options = question.options or {}

    if selected not in valid_options:
        return jsonify({"error": "Choose a valid answer option."}), 400

    correct = selected == question.correct_answer.upper()

    attempt = PracticeAttempt(
        user_id=current_user.id,
        question_id=question.id,
        selected_answer=selected,
        is_correct=correct
    )

    db.session.add(attempt)
    db.session.commit()

    return jsonify({
        "correct": correct,
        "correct_answer": question.correct_answer.upper(),
        "explanation": question.explanation or "No explanation has been added yet."
    })


@app.route("/api/qbank/questions/<int:question_id>/save",
           methods=["POST"])
@login_required
def qbank_toggle_saved(question_id):
    question = db.session.get(SATQuestion, question_id)

    if question is None:
        return jsonify({"error": "Question not found."}), 404

    existing = db.session.execute(
        db.select(SavedQuestion).where(
            SavedQuestion.user_id == current_user.id,
            SavedQuestion.question_id == question.id
        )
    ).scalar_one_or_none()

    if existing:
        db.session.delete(existing)
        saved = False
    else:
        db.session.add(SavedQuestion(
            user_id=current_user.id,
            question_id=question.id
        ))
        saved = True

    db.session.commit()

    return jsonify({"saved": saved})


@app.route("/api/qbank/stats", methods=["GET"])
@login_required
def qbank_stats():
    total = db.session.execute(
        db.select(db.func.count(PracticeAttempt.id)).where(
            PracticeAttempt.user_id == current_user.id
        )
    ).scalar_one()

    correct = db.session.execute(
        db.select(db.func.count(PracticeAttempt.id)).where(
            PracticeAttempt.user_id == current_user.id,
            PracticeAttempt.is_correct.is_(True)
        )
    ).scalar_one()

    saved = db.session.execute(
        db.select(db.func.count(SavedQuestion.id)).where(
            SavedQuestion.user_id == current_user.id
        )
    ).scalar_one()

    return jsonify({
        "attempts": total,
        "correct": correct,
        "saved": saved,
        "accuracy": round(correct / total * 100) if total else 0
    })


# =========================
# QBank Admin: Add Questions
# =========================


@app.route("/qbank/admin", methods=["GET", "POST"])
@login_required
def qbank_admin():
    if not current_user.is_admin:
        flash("You don't have permission to manage questions.", "error")
        return redirect(url_for("qbank"))

    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        topic = request.form.get("topic", "").strip()
        difficulty = request.form.get("difficulty", "").strip()
        question_text = request.form.get("question_text", "").strip()
        correct_answer = request.form.get("correct_answer", "").strip().upper()
        explanation = request.form.get("explanation", "").strip()

        options = {
            letter: request.form.get(f"option_{letter.lower()}", "").strip()
            for letter in ("A", "B", "C", "D")
        }

        if subject not in ("Math", "Reading & Writing"):
            flash("Choose a valid SAT section.", "error")
        elif not topic or len(topic) > 100:
            flash("Enter a valid topic (maximum 100 characters).", "error")
        elif difficulty not in ("Easy", "Medium", "Hard"):
            flash("Choose a valid difficulty.", "error")
        elif not question_text or len(question_text) > 20000:
            flash("Enter a question (maximum 20,000 characters).", "error")
        elif any(not value for value in options.values()):
            flash("Please fill in all four answer choices.", "error")
        elif correct_answer not in ("A", "B", "C", "D"):
            flash("Choose the correct answer.", "error")
        elif len(explanation) > 20000:
            flash("The explanation is too long.", "error")
        else:
            question = SATQuestion(
                subject=subject,
                topic=topic,
                difficulty=difficulty,
                question_text=question_text,
                options=options,
                correct_answer=correct_answer,
                explanation=explanation or None,
                source="manual",
                created_by=current_user.id
            )

            try:
                db.session.add(question)
                db.session.commit()
                flash("Question added to your Question Bank!", "success")
                return redirect(url_for("qbank_admin"))
            except Exception:
                db.session.rollback()
                app.logger.exception("Could not add SAT question")
                flash("Couldn't save the question. Please try again.", "error")

    # Load saved questions for the manager on every page visit.
    questions = SATQuestion.query.order_by(SATQuestion.id.desc()).all()

    return render_template("qbank_admin.html", questions=questions)


# Profile page and secure avatar upload

@app.route("/profile", methods=["GET", "POST"])

@login_required

def profile():

    upload_folder = os.path.join(

        app.static_folder, "uploads", "avatars"

    )



    if request.method == "POST":

        avatar = request.files.get("avatar")



        if not avatar or not avatar.filename:

            flash("Please choose a profile picture.", "error")

            return redirect(url_for("profile"))



        # Check the uploaded file's size (maximum 5 MB).

        image_bytes = avatar.read()



        if not image_bytes:

            flash("The uploaded file is empty.", "error")

            return redirect(url_for("profile"))



        if len(image_bytes) > 5 * 1024 * 1024:

            flash("The image must be 5 MB or smaller.", "error")

            return redirect(url_for("profile"))



        try:

            import io



            # Verify the actual image content, not just its filename.

            with Image.open(io.BytesIO(image_bytes)) as image:

                if image.format not in ("JPEG", "PNG", "WEBP"):

                    raise ValueError("Unsupported image format.")



                image.verify()



            # Reopen and re-encode the image to remove unwanted metadata.

            with Image.open(io.BytesIO(image_bytes)) as image:

                image.load()



                if image.width * image.height > 20_000_000:

                    raise ValueError("Image dimensions are too large.")



                image.thumbnail((512, 512))



                # Store a normalized JPEG with a random filename.

                image = image.convert("RGB")



                os.makedirs(upload_folder, exist_ok=True)



                filename = f"{uuid.uuid4().hex}.jpg"

                save_path = os.path.join(upload_folder, filename)

                image.save(save_path, format="JPEG", quality=85, optimize=True)



        except (UnidentifiedImageError, OSError, ValueError):

            flash(

                "Please upload a valid JPG, PNG, or WebP image.",

                "error",

            )

            return redirect(url_for("profile"))



        # Update the account only after the image saves successfully.

        old_filename = current_user.avatar_filename

        current_user.avatar_filename = filename



        try:

            db.session.commit()

        except Exception:

            db.session.rollback()



            if os.path.exists(save_path):

                os.remove(save_path)



            app.logger.exception("Profile picture update failed")

            flash("Couldn't save your profile picture. Please try again.", "error")

            return redirect(url_for("profile"))



        # Remove the previous avatar only after the new one is saved.

        if old_filename and old_filename != filename:

            old_path = os.path.join(upload_folder, old_filename)



            try:

                if os.path.isfile(old_path):

                    os.remove(old_path)

            except OSError:

                app.logger.warning("Could not remove previous avatar.")



        flash("Profile picture updated successfully!", "success")

        return redirect(url_for("profile"))



    return render_template("profile.html")







@app.errorhandler(RequestEntityTooLarge)

def handle_file_too_large(error):

    return jsonify({

        "error": "Image too large. The maximum upload size is 10 MB."

    }), 413





ALLOWED_IMAGE_TYPES = {

    "image/jpeg",

    "image/png",

    "image/webp",

}





@app.route("/ask", methods=["POST"])

@login_required

def ask():

    question = request.form.get("question", "").strip()

    section = request.form.get("section", "Math")

    image_file = request.files.get("image")



    if not question and not (image_file and image_file.filename):

        return jsonify({

            "error": "Please enter a question or upload an image."

        }), 400



    if section not in ("Math", "Reading & Writing"):

        return jsonify({"error": "Invalid SAT section."}), 400



    image_part = None



    if image_file and image_file.filename:

        if image_file.mimetype not in ALLOWED_IMAGE_TYPES:

            return jsonify({

                "error": "Unsupported image type. Use JPG, PNG, or WebP."

            }), 400



        image_bytes = image_file.read()



        if not image_bytes:

            return jsonify({"error": "The uploaded image is empty."}), 400



        image_part = types.Part.from_bytes(

            data=image_bytes,

            mime_type=image_file.mimetype,

        )



    api_key = os.getenv("GEMINI_API_KEY")



    if not api_key:

        return jsonify({

            "error": "Gemini API key missing. Check your .env file."

        }), 503



    prompt = f"""

You are SAT Tutor, a friendly and accurate SAT tutor.



SAT section: {section}

Student's written question or instructions: {question or "Please solve the question shown in the image."}



If an image is provided:

- Carefully read the question, equations, diagrams, and answer choices.

- Use the image as the source of the question.

- If text or a diagram is unclear, say exactly what you cannot read.

- Never invent missing numbers, labels, or answer choices.



Instructions:

- Explain the solution step by step in student-friendly language.

- For Math, show calculations and explain why each step works.

- For Reading & Writing, explain grammar, vocabulary, evidence, and reasoning.

- For multiple-choice questions, identify the correct option and explain why.

- Briefly explain why other options are incorrect when useful.

- If information is missing, ask the student for clarification.

- Never invent information.



Formatting:

- Use plain text with clear headings and numbered steps.

- Avoid unnecessary Markdown symbols.

- Write fractions as 1/2 and multiplication as ×.

"""



    try:

        client = genai.Client(api_key=api_key)

        contents = [prompt]



        if image_part is not None:

            contents.append(image_part)



        response = client.models.generate_content(

            model="gemini-3.6-flash",

            contents=contents,

        )



        answer = response.text



        if not answer:

            return jsonify({

                "error": "The AI returned an empty answer. Please retry."

            }), 502



        return jsonify({"answer": answer})



    except Exception:

        app.logger.exception("Gemini request failed")

        return jsonify({

            "error": (

                "The AI request failed. Check your API key, internet "

                "connection, model availability, and API quota."

            )

        }), 502







with app.app_context():
    # create_all() is idempotent — safe to call on every startup.
    # In production you would use Flask-Migrate for proper schema migrations,
    # but create_all() is fine while the schema is still evolving.
    db.create_all()





# Grant admin permissions to the existing Ali account.



# Grant admin permissions to the existing Ali account.

with app.app_context():

    ali = db.session.execute(

        db.select(User).where(User.username == "Ali")

    ).scalar_one_or_none()



    if ali is not None and not ali.is_admin:

        ali.is_admin = True

        db.session.commit()



if __name__ == "__main__":

    app.run(debug=True)


