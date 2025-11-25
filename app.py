from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from collections import defaultdict
import os

from config import config
from questions.main_questions import QUESTIONS
from questions.tie_breaker_questions import TIE_BREAKER_QUESTIONS

def create_app():
    app = Flask(__name__)
    
    # Configuration
    env = os.environ.get('FLASK_ENV', 'default')
    app.config.from_object(config[env])
    
    return app

app = create_app()

def initialize_session():
    """Initialize session variables"""
    session['current_question'] = 1
    session['answers'] = {}
    session['riasec_scores'] = {'R': 0, 'I': 0, 'A': 0, 'S': 0, 'E': 0, 'C': 0}
    session['aptitude_scores'] = defaultdict(int)
    session['tie_breaker_phase'] = False
    session['tie_breaker_questions'] = []
    session['tie_breaker_answered'] = 0
    session['total_questions'] = len(QUESTIONS)

def calculate_scores():
    """Calculate RIASEC and aptitude scores from answers"""
    riasec_scores = {'R': 0, 'I': 0, 'A': 0, 'S': 0, 'E': 0, 'C': 0}
    aptitude_scores = defaultdict(int)
    
    for q_num, answer in session['answers'].items():
        if not session['tie_breaker_phase'] or int(q_num) <= 30:
            question = next((q for q in QUESTIONS if q['number'] == int(q_num)), None)
        else:
            question = next((q for q in TIE_BREAKER_QUESTIONS if q['number'] == int(q_num)), None)
            
        if question and answer in question['options']:
            option = question['options'][answer]
            # Add RIASEC score
            riasec_scores[option['riasec']] += 1
            
            # Add aptitude scores (only for main questions)
            if not session['tie_breaker_phase'] or int(q_num) <= 30:
                for aptitude, score in option.get('aptitudes', {}).items():
                    aptitude_scores[aptitude] += score
    
    return riasec_scores, dict(aptitude_scores)

def get_top_three(scores_dict):
    """Get top 3 items from a scores dictionary"""
    sorted_items = sorted(scores_dict.items(), key=lambda x: x[1], reverse=True)
    return sorted_items[:3]

def needs_tie_breaker(riasec_scores):
    """Check if tie-breaker questions are needed using configurable delta"""
    delta = app.config['TIE_BREAKER_DELTA']
    sorted_scores = sorted(riasec_scores.items(), key=lambda x: x[1], reverse=True)
    
    if len(sorted_scores) < 3:
        return True
    
    top_scores = sorted_scores[:3]
    first_score = top_scores[0][1]
    second_score = top_scores[1][1]
    third_score = top_scores[2][1]
    
    # Check if differences meet the delta requirement
    first_second_diff = first_score - second_score
    second_third_diff = second_score - third_score
    
    # Check for ties beyond third place
    has_ties_beyond = len([score for code, score in sorted_scores if score == third_score]) > 1
    
    # Need tie-breaker if differences are less than delta or there are ties
    result = not ((first_second_diff >= delta) and (second_third_diff >= delta) and not has_ties_beyond)
    return result

def get_tie_breaker_questions(riasec_scores):
    """Get tie-breaker questions based on tied codes"""
    sorted_scores = sorted(riasec_scores.items(), key=lambda x: x[1], reverse=True)
    
    needed_pairs = set()
    first_score, second_score, third_score = sorted_scores[0][1], sorted_scores[1][1], sorted_scores[2][1]
    delta = app.config['TIE_BREAKER_DELTA']
    
    if first_score - second_score < delta:
        needed_pairs.add(frozenset([sorted_scores[0][0], sorted_scores[1][0]]))
    if second_score - third_score < delta:
        needed_pairs.add(frozenset([sorted_scores[1][0], sorted_scores[2][0]]))
    
    third_place_codes = [code for code, score in sorted_scores if score == third_score]
    if len(third_place_codes) > 1:
        for i in range(len(third_place_codes)):
            for j in range(i + 1, len(third_place_codes)):
                needed_pairs.add(frozenset([third_place_codes[i], third_place_codes[j]]))
    
    tie_questions = []
    for pair in needed_pairs:
        pair_list = sorted(list(pair))
        pair_str = f"{pair_list[0]}-{pair_list[1]}"
        pair_questions = [q for q in TIE_BREAKER_QUESTIONS if q['pair'] == pair_str]
        if pair_questions:
            tie_questions.extend(pair_questions[:2])
        else:
            pair_str_rev = f"{pair_list[1]}-{pair_list[0]}"
            pair_questions = [q for q in TIE_BREAKER_QUESTIONS if q['pair'] == pair_str_rev]
            if pair_questions:
                tie_questions.extend(pair_questions[:2])
    
    return tie_questions

def should_continue_tie_breakers(riasec_scores):
    """Check if we need more tie-breaker questions after current set"""
    return needs_tie_breaker(riasec_scores)

# Routes (same as before)
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start_assessment():
    initialize_session()
    return redirect(url_for('assessment'))

@app.route('/assessment')
def assessment():
    if 'current_question' not in session:
        return redirect(url_for('index'))
    
    current_q = session['current_question']
    
    if not session['tie_breaker_phase']:
        if current_q <= len(QUESTIONS):
            question = QUESTIONS[current_q - 1]
            return render_template('assessment.html', 
                                   question=question, 
                                   phase="main",
                                   total_questions=len(QUESTIONS),
                                   current_question=current_q)
        else:
            riasec_scores, _ = calculate_scores()
            
            if needs_tie_breaker(riasec_scores):
                session['tie_breaker_phase'] = True
                tie_questions = get_tie_breaker_questions(riasec_scores)
                session['tie_breaker_questions'] = tie_questions
                session['tie_breaker_answered'] = 0
                session['total_questions'] = len(QUESTIONS) + len(tie_questions)
                return redirect(url_for('assessment'))
            else:
                return redirect(url_for('submit_all_answers'))
    else:
        tie_questions = session['tie_breaker_questions']
        tie_answered = session['tie_breaker_answered']
        
        if tie_answered < len(tie_questions):
            question = tie_questions[tie_answered]
            current_q = len(QUESTIONS) + tie_answered + 1
            return render_template('assessment.html', 
                                   question=question, 
                                   phase="tie_breaker",
                                   total_questions=session['total_questions'],
                                   current_question=current_q)
        else:
            riasec_scores, _ = calculate_scores()
            if should_continue_tie_breakers(riasec_scores):
                additional_questions = get_tie_breaker_questions(riasec_scores)
                if additional_questions:
                    session['tie_breaker_questions'].extend(additional_questions)
                    session['total_questions'] += len(additional_questions)
                    return redirect(url_for('assessment'))
            
            return redirect(url_for('submit_all_answers'))

@app.route('/save_answer', methods=['POST'])
def save_answer():
    if 'current_question' not in session:
        return jsonify({'success': False, 'redirect': url_for('index')})
    
    data = request.get_json()
    question_number = data.get('question_number')
    answer = data.get('answer')
    
    if question_number and answer:
        session['answers'][str(question_number)] = answer
        session.modified = True
        
        if not session['tie_breaker_phase']:
            session['current_question'] += 1
        else:
            session['tie_breaker_answered'] += 1
            session['current_question'] = len(QUESTIONS) + session['tie_breaker_answered'] + 1
        
        return jsonify({'success': True, 'redirect': url_for('assessment')})
    
    return jsonify({'success': False})

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

# Production-ready check
if __name__ == '__main__':
    # For production, use a proper WSGI server like Gunicorn
    if app.config['DEBUG']:
        app.run(debug=True)
    else:
        port = os.getenv("port")
        app.run(host='0.0.0.0', port=(port, 5000))