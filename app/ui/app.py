from flask import Flask, render_template, request, jsonify, send_file
from app.database.mongodb import get_db
from bson import ObjectId
from datetime import datetime
import os
import json

# Get template directory
template_dir = os.path.join(os.path.dirname(__file__), 'templates')
app = Flask(__name__, template_folder=template_dir)

# Custom JSON encoder for ObjectId
class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)

app.json_encoder = JSONEncoder

@app.route('/')
def index():
    """Home page with table, pagination and search"""
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    """API endpoint to fetch batch results with pagination and search - optimized for large datasets"""
    try:
        # Get query parameters
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))
        search = request.args.get('search', '').strip()
        
        # Calculate skip for pagination
        skip = (page - 1) * per_page
        
        # Get MongoDB collection
        collection = get_db().batch_results
        
        # Build aggregation pipeline for efficient querying
        pipeline = []
        
        # First unwind results array to get each symbol's data
        pipeline.append({'$unwind': '$results'})
        
        # Then unwind strategies array to get each strategy
        pipeline.append({'$unwind': '$results.strategies'})
        
        # Add search filter if provided (after unwinding for accurate search)
        if search:
            search_regex = {'$regex': search, '$options': 'i'}
            pipeline.append({
                '$match': {
                    '$or': [
                        {'results.strategies.strategy_name': search_regex},
                        {'results.symbol': search_regex}
                    ]
                }
            })
        
        # Sort by created_at descending (newest first)
        pipeline.append({'$sort': {'created_at': -1}})
        
        # Use $facet to get both data and total count in one query
        facet_stage = {
            '$facet': {
                'data': [
                    {'$skip': skip},
                    {'$limit': per_page},
                    {
                        '$project': {
                            '_id': 1,
                            'batch_id': {'$toString': '$_id'},
                            'datetime': '$created_at',
                            'pubsub': '$pubsub',
                            'strategy_name': '$results.strategies.strategy_name',
                            'symbol': '$results.symbol',
                            'signal_type': '$results.strategies.signal_type',
                            'confidence': '$results.strategies.confidence',
                            'price': '$results.strategies.price',
                            'execution_time': '$results.strategies.execution_time',
                            'timestamp': '$results.strategies.timestamp',
                            'success': '$results.strategies.success'
                        }
                    }
                ],
                'total': [
                    {'$count': 'count'}
                ]
            }
        }
        pipeline.append(facet_stage)
        
        # Execute aggregation
        result = list(collection.aggregate(pipeline))
        
        if not result:
            return jsonify({
                'data': [],
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': 0,
                    'total_pages': 1
                }
            })
        
        # Extract data and total count
        facet_result = result[0]
        data = facet_result.get('data', [])
        total_count = facet_result.get('total', [{}])[0].get('count', 0) if facet_result.get('total') else 0
        
        # Convert ObjectId to string and datetime objects to ISO strings for JSON serialization
        for item in data:
            # Convert _id to batch_id string if not already converted
            if '_id' in item and not item.get('batch_id'):
                item['batch_id'] = str(item['_id'])
            elif '_id' in item:
                item.pop('_id', None)
            
            # Ensure batch_id is a string
            if 'batch_id' in item and isinstance(item['batch_id'], ObjectId):
                item['batch_id'] = str(item['batch_id'])
            
            # Convert datetime objects to ISO strings
            if isinstance(item.get('datetime'), datetime):
                item['datetime'] = item['datetime'].isoformat()
            if isinstance(item.get('timestamp'), datetime):
                item['timestamp'] = item['timestamp'].isoformat()
        
        # Calculate total pages
        total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 1
        
        return jsonify({
            'data': data,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total_count,
                'total_pages': total_pages
            }
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

@app.route('/logs')
def logs():
    """Logs page showing list of log files"""
    return render_template('logs.html')

@app.route('/api/logs/files')
def get_log_files():
    """API endpoint to get list of log files"""
    try:
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
        if not os.path.exists(logs_dir):
            return jsonify({'files': []})
        
        files = []
        for filename in os.listdir(logs_dir):
            filepath = os.path.join(logs_dir, filename)
            if os.path.isfile(filepath) and filename.endswith('.log'):
                file_stat = os.stat(filepath)
                files.append({
                    'name': filename,
                    'size': file_stat.st_size,
                    'modified': datetime.fromtimestamp(file_stat.st_mtime).isoformat()
                })
        
        files.sort(key=lambda x: x['modified'], reverse=True)
        return jsonify({'files': files})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs/<filename>')
def get_log_content(filename):
    """API endpoint to get log file content - shows last 1000 lines with latest first"""
    try:
        # Security: prevent directory traversal
        filename = os.path.basename(filename)
        if not filename.endswith('.log'):
            return jsonify({'error': 'Invalid file'}), 400
        
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
        filepath = os.path.join(logs_dir, filename)
        
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        # Read file content and get last 1000 lines
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        # Get last 1000 lines and reverse them (latest first)
        last_lines = lines[-1000:] if len(lines) > 1000 else lines
        last_lines.reverse()  # Latest first
        content = ''.join(last_lines)
        
        return jsonify({
            'filename': filename,
            'content': content,
            'total_lines': len(lines),
            'showing_lines': len(last_lines)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs/<filename>/download')
def download_log(filename):
    """API endpoint to download full log file"""
    try:
        # Security: prevent directory traversal
        filename = os.path.basename(filename)
        if not filename.endswith('.log'):
            return jsonify({'error': 'Invalid file'}), 400
        
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
        filepath = os.path.join(logs_dir, filename)
        
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        return send_file(
            filepath,
            as_attachment=True,
            download_name=filename,
            mimetype='text/plain'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/logs/<filename>')
def view_log(filename):
    """Page to view log file content"""
    return render_template('log_viewer.html', filename=filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

