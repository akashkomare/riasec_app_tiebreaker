# app.py
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from collections import defaultdict
from copy import deepcopy
import os
from dotenv import load_dotenv
from config import config
from questions.main_questions import QUESTIONS
from questions.tie_breaker_questions import TIE_BREAKER_QUESTIONS

load_dotenv()

# --- Configuration / App factory ------------------------------------------------
def create_app():
    app = Flask(__name__)

    # Configuration
    env = os.environ.get('FLASK_ENV', 'default')
    app.config.from_object(config[env])

    # sensible defaults if not provided
    if 'TIE_BREAKER_DELTA' not in app.config:
        app.config['TIE_BREAKER_DELTA'] = 1
    if 'MAX_TIE_BREAKER_ROUNDS' not in app.config:
        app.config['MAX_TIE_BREAKER_ROUNDS'] = 3  # Max questions per pair

    return app

app = create_app()

# --- Session init ----------------------------------------------------------------
def initialize_session():
    """Initialize session variables"""
    session['current_question'] = 1
    session['answers'] = {}  # keys: strings of question_number, values: selected option key
    session['riasec_scores'] = {'R': 0, 'I': 0, 'A': 0, 'S': 0, 'E': 0, 'C': 0}
    session['aptitude_scores'] = {}  # will be computed on submit
    session['tie_breaker_phase'] = False
    session['tie_breaker_questions'] = []  # list of dicts (copied) with unique 'number' assigned
    session['tie_breaker_answered'] = 0
    session['tie_breaker_round'] = 0
    session['active_tie_pairs'] = []  # Store as list instead of set
    session['completed_tie_pairs'] = []  # Store as list instead of set
    session['pair_question_count'] = {}  # Track how many questions asked per pair
    session['total_questions'] = len(QUESTIONS)
    session.modified = True

# --- Scoring ---------------------------------------------------------------------
def calculate_scores():
    """
    Calculate RIASEC and aptitude scores from answers.
    Only counts:
      - main questions from QUESTIONS
      - tie-breaker questions that were assigned into session['tie_breaker_questions']
    """
    riasec_scores = {'R': 0, 'I': 0, 'A': 0, 'S': 0, 'E': 0, 'C': 0}
    aptitude_scores = defaultdict(int)

    # Build lookup for main and session tie-breakers
    main_lookup = {q['number']: q for q in QUESTIONS}
    tie_lookup = {q['number']: q for q in session.get('tie_breaker_questions', [])}

    for q_num_str, answer in session.get('answers', {}).items():
        try:
            q_num = int(q_num_str)
        except (ValueError, TypeError):
            continue

        question = main_lookup.get(q_num) or tie_lookup.get(q_num)
        if not question:
            # question not found in either set -> skip
            continue

        # validate option exists in question
        if answer not in question.get('options', {}):
            continue

        option = question['options'][answer]

        # add RIASEC
        riasec_code = option.get('riasec')
        if riasec_code and riasec_code in riasec_scores:
            riasec_scores[riasec_code] += 1

        # aptitude only from main questions (we treat main questions as those with number <= len(QUESTIONS))
        if q_num <= len(QUESTIONS):
            for aptitude, score in option.get('aptitudes', {}).items():
                aptitude_scores[aptitude] += score

    return riasec_scores, dict(aptitude_scores)

def get_top_three(scores_dict):
    """Get top 3 items from a scores dictionary"""
    sorted_items = sorted(scores_dict.items(), key=lambda x: x[1], reverse=True)
    return sorted_items[:3]

def get_current_riasec_code(riasec_scores):
    """Get current RIASEC code from scores"""
    top_three = get_top_three(riasec_scores)
    return ''.join([code for code, score in top_three])

# --- Tie-breaker decision & question selection -----------------------------------
def needs_tie_breaker_for_pair(pair, riasec_scores):
    """
    Check if a specific pair still needs tie-breaking based on delta threshold.
    Returns True if the difference is < delta (changed from < delta).
    """
    if len(pair) != 2:
        return False
        
    score1 = riasec_scores.get(pair[0], 0)
    score2 = riasec_scores.get(pair[1], 0)
    delta = app.config.get('TIE_BREAKER_DELTA', 1)
    
    # If difference is < delta, need tie-breaker (fixed from < delta)
    return abs(score1 - score2) < delta


def select_tie_breaker_pairs(riasec_scores):
    """
    Return a list of sorted tuple pairs of codes that need tie-breakers.
    ONLY considers the TOP 3 codes by score.
    
    Logic:
    1. Get top 3 codes by score
    2. Check pairs within top 3 where score difference < delta
    3. Returns pairs in order: (1st vs 2nd), (2nd vs 3rd), (1st vs 3rd) if needed
    """
    # Sort all codes by score (descending), then alphabetically for consistency
    sorted_scores = sorted(riasec_scores.items(), key=lambda x: (-x[1], x[0]))
    
    if len(sorted_scores) < 2:
        return []
    
    # ONLY get top 3 codes
    top_three = sorted_scores[:3]
    
    delta = app.config.get('TIE_BREAKER_DELTA', 1)
    pairs = []
    
    # Check all pairs within top 3
    for i in range(len(top_three)):
        for j in range(i + 1, len(top_three)):
            code1, score1 = top_three[i]
            code2, score2 = top_three[j]
            
            # Check if difference is within delta threshold
            if abs(score1 - score2) < delta:
                # Create normalized pair (alphabetically sorted)
                pair = tuple(sorted([code1, code2]))
                if pair not in pairs:
                    pairs.append(pair)
    
    return pairs


def get_next_tie_breaker_question_for_pairs(active_pairs):
    """
    Get the next tie-breaker question for active pairs.
    Returns a single question for the FIRST active pair that needs one.
    """
    # Create lookup mapping of normalized pair -> list of questions
    pair_map = {}
    for q in TIE_BREAKER_QUESTIONS:
        p = q.get('pair', '')
        if not p:
            continue
        
        # Normalize pair string (alphabetically sorted)
        codes = p.split('-')
        normalized = '-'.join(sorted(codes))
        
        pair_map.setdefault(normalized, []).append(q)
    
    # Try to find a question for each active pair (in order)
    for pair in active_pairs:
        # Normalize the pair tuple to string
        pair_str = '-'.join(sorted([pair[0], pair[1]]))
        
        # Get questions for this pair
        candidate_questions = pair_map.get(pair_str, [])
        
        if not candidate_questions:
            continue
        
        # Check which questions haven't been asked yet for this pair
        asked_questions = set()
        for tq in session.get('tie_breaker_questions', []):
            # Get the normalized pair from the tie question
            tq_pair = tq.get('pair', '')
            if tq_pair:
                tq_codes = tq_pair.split('-')
                tq_normalized = '-'.join(sorted(tq_codes))
                
                if tq_normalized == pair_str:
                    # Track by original question number
                    original_num = tq.get('original_number', tq.get('number'))
                    asked_questions.add(original_num)
        
        # Find the first unasked question
        for question in candidate_questions:
            original_num = question.get('number', id(question))
            if original_num not in asked_questions:
                # Create a copy and mark with metadata
                question_copy = deepcopy(question)
                question_copy['original_number'] = original_num
                question_copy['pair'] = pair_str  # Store normalized pair string
                return question_copy, pair_str

    return None, None



def assign_unique_number_to_tie_question(question):
    """
    Give a tie question a unique 'number' that does not collide with main question numbers.
    """
    start = len(QUESTIONS) + len(session.get('tie_breaker_questions', [])) + 1
    question['number'] = start
    return question


def update_pair_question_count(pair_str):
    """Update the question count for a pair"""
    if 'pair_question_count' not in session:
        session['pair_question_count'] = {}
    
    # Normalize pair string (always alphabetically sorted)
    codes = pair_str.split('-')
    normalized_pair = '-'.join(sorted(codes))
    
    if normalized_pair not in session['pair_question_count']:
        session['pair_question_count'][normalized_pair] = 0
    session['pair_question_count'][normalized_pair] += 1
    session.modified = True


def has_reached_max_questions_for_pair(pair_str):
    """Check if a pair has reached its maximum allowed questions"""
    max_questions = app.config.get('MAX_TIE_BREAKER_ROUNDS', 3)
    
    # Normalize pair string
    codes = pair_str.split('-')
    normalized_pair = '-'.join(sorted(codes))
    
    current_count = session.get('pair_question_count', {}).get(normalized_pair, 0)
    return current_count >= max_questions


# --- Routes ----------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/start', methods=['POST'])
def start_assessment():
    initialize_session()
    return redirect(url_for('assessment'))


@app.route('/assessment')
@app.route('/assessment')
def assessment():
    # if session not initialized, redirect to index
    if 'current_question' not in session:
        return redirect(url_for('index'))

    current_q = session['current_question']

    # Main phase
    if not session.get('tie_breaker_phase', False):
        if current_q <= len(QUESTIONS):
            question = QUESTIONS[current_q - 1]
            return render_template('assessment.html',
                                   question=question,
                                   phase="main",
                                   total_questions=session.get('total_questions', len(QUESTIONS)),
                                   current_question=current_q)
        else:
            # Finished main questions -> evaluate for tie-breakers
            riasec_scores, _ = calculate_scores()
            active_pairs = select_tie_breaker_pairs(riasec_scores)
            
            if active_pairs:
                # Remove already completed pairs
                completed_pairs = session.get('completed_tie_pairs', [])
                active_pairs = [pair for pair in active_pairs if pair not in completed_pairs]
                
                if active_pairs:
                    session['active_tie_pairs'] = active_pairs
                    session['tie_breaker_phase'] = True
                    session['tie_breaker_answered'] = 0
                    session.modified = True
                    return redirect(url_for('assessment'))
            
            # No active pairs or all pairs completed
            return redirect(url_for('submit_all_answers'))

    # Tie-breaker phase - KEY CHANGE: Re-evaluate after EVERY answer
    else:
        # Recalculate scores to check current state
        riasec_scores, _ = calculate_scores()
        
        # Re-evaluate which pairs still need tie-breaking (ONLY top 3)
        all_potential_pairs = select_tie_breaker_pairs(riasec_scores)
        
        # Filter to only pairs that are still active and unresolved
        still_active_pairs = []
        completed_pairs = session.get('completed_tie_pairs', [])
        
        for pair in all_potential_pairs:
            # Skip if already completed
            if pair in completed_pairs:
                continue
            
            pair_str = '-'.join(sorted([pair[0], pair[1]]))
            
            # Check if pair has reached max questions
            if has_reached_max_questions_for_pair(pair_str):
                # Mark as completed due to exhaustion
                if pair not in completed_pairs:
                    completed_pairs.append(pair)
                continue
            
            # Check if tie is still unresolved (using < delta)
            if needs_tie_breaker_for_pair(pair, riasec_scores):
                still_active_pairs.append(pair)
            else:
                # Mark as completed due to resolution
                if pair not in completed_pairs:
                    completed_pairs.append(pair)
        
        session['active_tie_pairs'] = still_active_pairs
        session['completed_tie_pairs'] = completed_pairs
        session.modified = True
        
        if not still_active_pairs:
            # All active pairs are resolved or exhausted
            return redirect(url_for('submit_all_answers'))

        # Get current tie-breaker questions
        tie_questions = session.get('tie_breaker_questions', [])
        tie_answered = session.get('tie_breaker_answered', 0)

        # Check if we need to show an existing unanswered question
        if tie_answered < len(tie_questions):
            # Show next unanswered tie-breaker question
            question = tie_questions[tie_answered]
            current_q_number = len(QUESTIONS) + tie_answered + 1
            return render_template('assessment.html',
                                   question=question,
                                   phase="tie_breaker",
                                   total_questions=session.get('total_questions', len(QUESTIONS) + len(tie_questions)),
                                   current_question=current_q_number)
        
        # Need to get next tie-breaker question (one at a time)
        next_question, pair_str = get_next_tie_breaker_question_for_pairs(still_active_pairs)
        
        if next_question:
            # Assign unique number and add to session
            numbered_question = assign_unique_number_to_tie_question(next_question)
            session['tie_breaker_questions'].append(numbered_question)
            session['total_questions'] = len(QUESTIONS) + len(session['tie_breaker_questions'])
            
            # Update pair question count
            update_pair_question_count(pair_str)
            
            session.modified = True
            return redirect(url_for('assessment'))
        else:
            # No more questions available for active pairs
            # Mark remaining pairs as completed
            for pair in still_active_pairs:
                if pair not in completed_pairs:
                    completed_pairs.append(pair)
            session['completed_tie_pairs'] = completed_pairs
            session.modified = True
            return redirect(url_for('submit_all_answers'))
        
        
# --- Answer saving ---------------------------------------------------------------
@app.route('/save_answer', methods=['POST'])
def save_answer():
    if 'current_question' not in session:
        return jsonify({'success': False, 'redirect': url_for('index')})

    data = request.get_json()
    question_number = data.get('question_number')
    answer = data.get('answer')

    if not question_number or answer is None:
        return jsonify({'success': False})

    # store as string for JSON-compatibility in session
    session['answers'][str(question_number)] = answer
    session.modified = True

    # if still in main phase, increment current_question
    if not session.get('tie_breaker_phase', False):
        session['current_question'] = session.get('current_question', 1) + 1
    else:
        # update tie breaker answered index and current question pointer
        session['tie_breaker_answered'] = session.get('tie_breaker_answered', 0) + 1
        # set current_question to the next tie question number for bookkeeping/UI (not used for lookup)
        session['current_question'] = len(QUESTIONS) + session['tie_breaker_answered'] + 1

    session.modified = True
    return jsonify({'success': True, 'redirect': url_for('assessment')})


# --- Live Scores Endpoint --------------------------------------------------------
@app.route('/get_current_scores')
def get_current_scores():
    """Endpoint to get current scores for live display"""
    try:
        riasec_scores, aptitude_scores = calculate_scores()
        top_riasec = get_top_three(riasec_scores)
        riasec_code = get_current_riasec_code(riasec_scores)
        
        return jsonify({
            'riasec_scores': riasec_scores,
            'aptitude_scores': aptitude_scores,
            'top_riasec': top_riasec,
            'riasec_code': riasec_code,
            'error': None
        })
    except Exception as e:
        return jsonify({'error': str(e)})


# --- Submit & Results ------------------------------------------------------------
@app.route('/submit_all_answers', methods=['GET', 'POST'])
def submit_all_answers():
    if 'answers' not in session or len(session['answers']) == 0:
        return redirect(url_for('index'))
    return redirect(url_for('results'))


@app.route('/results')
def results():
    if 'answers' not in session or len(session['answers']) == 0:
        return redirect(url_for('index'))

    riasec_scores, aptitude_scores = calculate_scores()
    top_riasec = get_top_three(riasec_scores)
    top_aptitudes = get_top_three(aptitude_scores)

    riasec_code = ''.join([code for code, score in top_riasec])

    max_riasec_score = max(riasec_scores.values()) if riasec_scores else 1
    max_aptitude_score = max(aptitude_scores.values()) if aptitude_scores else 1

    return render_template('results.html',
                           riasec_code=riasec_code,
                           top_riasec=top_riasec,
                           top_aptitudes=top_aptitudes,
                           all_riasec_scores=riasec_scores,
                           all_aptitude_scores=aptitude_scores,
                           max_riasec_score=max_riasec_score,
                           max_aptitude_score=max_aptitude_score)


@app.route('/restart')
def restart():
    session.clear()
    return redirect(url_for('index'))


# --- Run (development only) ------------------------------------------------------
if __name__ == '__main__':
    if app.config.get('DEBUG', False):
        app.run(debug=True)
    else:
        port_env = os.getenv('port') or os.getenv('PORT') or "5000"
        try:
            port = int(port_env)
        except ValueError:
            port = 5000
        app.run(host='0.0.0.0', port=port)