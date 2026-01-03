from flask import Flask, render_template, request, jsonify, send_file, redirect
from app.database.mongodb import get_database as get_db
from bson import ObjectId
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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
def root():
    """Redirect root to broker dashboard"""
    return redirect('/broker/dashboard')

@app.route('/signals')
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
        show_buy_sell_only = request.args.get('show_buy_sell_only') == 'true'
        
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
        
        # Build match filter
        match_query = {}
        
        # Add search filter if provided
        if search:
            search_regex = {'$regex': search, '$options': 'i'}
            match_query['$or'] = [
                {'results.strategies.strategy_name': search_regex},
                {'results.symbol': search_regex},
                {'results.strategies.signal_type': search_regex}
            ]
            
        # Add Buy/Sell filter if enabled
        if show_buy_sell_only:
            match_query['results.strategies.signal_type'] = {'$in': ['BUY', 'SELL', 'buy', 'sell']}
            
        # Apply match if any filters exist
        if match_query:
            pipeline.append({'$match': match_query})
        
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
            
            # Convert datetime objects to ISO strings (UTC)
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

@app.route('/broker/orders')
def orders_page():
    """Orders page"""
    return render_template('orders.html')

@app.route('/broker/positions')
def positions_page():
    """Positions page"""
    return render_template('positions.html')

@app.route('/api/orders')
def get_orders():
    """API endpoint to fetch orders from trade_buddy db"""
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))
        show_buy_sell_only = request.args.get('show_buy_sell_only') == 'true'
        
        skip = (page - 1) * per_page
        
        # Access trade_buddy database
        # We use the client from the existing connection to access a different DB
        db = get_db().client['trade_buddy']
        collection = db['orders']
        
        query = {}
        if show_buy_sell_only:
            query['side'] = {'$in': ['buy', 'sell', 'BUY', 'SELL']}
            
        total_count = collection.count_documents(query)
        
        orders = list(collection.find(query)
                     .sort('created_at', -1)
                     .skip(skip)
                     .limit(per_page))
        
        # Process for JSON
        for item in orders:
            if '_id' in item:
                item['_id'] = str(item['_id'])
            # Ensure dates are strings ? In the sample they are strings "2025..."
            # If they are datetime objects, encoder handles it.
            
        total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 1
        
        return jsonify({
            'data': orders,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total_count,
                'total_pages': total_pages
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/positions')
def get_positions():
    """API endpoint to fetch positions from trade_buddy db"""
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))
        
        skip = (page - 1) * per_page
        
        db = get_db().client['trade_buddy']
        collection = db['positions']
        
        query = {}
        
        total_count = collection.count_documents(query)
        
        positions = list(collection.find(query)
                        .sort('created_at', -1)
                        .skip(skip)
                        .limit(per_page))
                        
        for item in positions:
            if '_id' in item:
                item['_id'] = str(item['_id'])
                
        total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 1
        
        return jsonify({
            'data': positions,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total_count,
                'total_pages': total_pages
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/broker/dashboard')
def broker_dashboard():
    """Broker Dashboard page"""
    return render_template('broker_dashboard.html')

@app.route('/api/broker/stats')
def get_broker_stats():
    """API to fetch aggregated stats for the dashboard"""
    try:
        db = get_db().client['trade_buddy']
        positions_col = db['positions']
        orders_col = db['orders']
        
        # 1. Calculate Total Commission from Orders
        commission_pipeline = [
            {
                '$group': {
                    '_id': None,
                    'total_commission': {'$sum': {'$toDouble': {'$ifNull': ['$paid_commission', '0']}}}
                }
            }
        ]
        comm_result = list(orders_col.aggregate(commission_pipeline))
        total_commission = comm_result[0]['total_commission'] if comm_result else 0
        
        # 2. Pipeline for global stats and daily graph from Positions
        pipeline = [
            # Match only closed positions for realized PnL analysis
            {'$match': {'status': 'closed'}},
            
            # Convert values and extract date safely
            {'$addFields': {
                'pnl_value': {'$toDouble': '$realized_pnl'},
                'margin_value': {'$toDouble': {'$ifNull': ['$margin', '0']}},
                # Use $dateToString for correct daily grouping from Date objects
                'date': {
                    '$dateToString': {
                        'format': '%Y-%m-%d', 
                        'date': '$created_at',
                        'timezone': 'Asia/Kolkata' # Use specific timezone to ensure correct daily grouping
                    }
                }
            }},
            
            # Facet for two different aggregations
            {'$facet': {
                # A: Global Summary
                'summary': [
                    {'$group': {
                        '_id': None,
                        'total_pnl': {'$sum': '$pnl_value'},
                        'total_margin': {'$sum': '$margin_value'},
                        'total_trades': {'$sum': 1},
                        'winning_trades': {
                            '$sum': {'$cond': [{'$gt': ['$pnl_value', 0]}, 1, 0]}
                        },
                        'losing_trades': {
                            '$sum': {'$cond': [{'$lt': ['$pnl_value', 0]}, 1, 0]}
                        }
                    }}
                ],
                # B: Daily PnL for Graph
                'daily': [
                    {'$group': {
                        '_id': '$date',
                        'daily_pnl': {'$sum': '$pnl_value'},
                        'daily_trades': {'$sum': 1}
                    }},
                    {'$sort': {'_id': 1}}  # Sort by date ascending
                ],
                # C: Symbol Breakdown
                'by_symbol': [
                    {'$group': {
                        '_id': '$symbol',
                        'total_profit': {'$sum': {'$cond': [{'$gt': ['$pnl_value', 0]}, '$pnl_value', 0]}},
                        'total_loss': {'$sum': {'$cond': [{'$lt': ['$pnl_value', 0]}, '$pnl_value', 0]}},
                        'net_pnl': {'$sum': '$pnl_value'},
                        'trade_count': {'$sum': 1},
                        'winning_trades': {'$sum': {'$cond': [{'$gt': ['$pnl_value', 0]}, 1, 0]}},
                        'losing_trades': {'$sum': {'$cond': [{'$lt': ['$pnl_value', 0]}, 1, 0]}}
                    }},
                    {'$sort': {'net_pnl': -1}} # Sort by highest profit
                ]
            }}
        ]
        
        result = list(positions_col.aggregate(pipeline))
        
        # Process results
        data = result[0]
        summary = data['summary'][0] if data['summary'] else {
            'total_pnl': 0, 'total_margin': 0, 'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0
        }
        
        # Add commission from orders
        summary['total_commission'] = total_commission
        
        # Calculate Stats
        total_trades = summary.get('total_trades', 0)
        winning_trades = summary.get('winning_trades', 0)
        total_margin = summary.get('total_margin', 0)
        
        # Winrate
        winrate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        summary['winrate'] = winrate
        
        # ROI
        total_pnl = summary.get('total_pnl', 0)
        roi = (total_pnl / total_margin * 100) if total_margin > 0 else 0
        summary['roi'] = roi
        
        daily_data = data['daily']
        symbol_data = data['by_symbol']
        
        return jsonify({
            'summary': summary,
            'daily': daily_data,
            'by_symbol': symbol_data
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

