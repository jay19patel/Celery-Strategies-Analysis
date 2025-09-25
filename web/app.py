from flask import Flask, render_template, jsonify, redirect, url_for, send_from_directory
import os
import glob
import json


app = Flask(__name__, template_folder="templates", static_folder="static")


def _list_result_files() -> list[str]:
    os.makedirs("results", exist_ok=True)
    return sorted(glob.glob(os.path.join("results", "results_*.json")))


@app.route("/")
def index():
    files = _list_result_files()
    latest_data = None
    if files:
        with open(files[-1]) as f:
            latest_data = json.load(f)
    return render_template("index.html", latest=latest_data, files=[os.path.basename(f) for f in files])


@app.route("/api/latest")
def api_latest():
    files = _list_result_files()
    if not files:
        return jsonify({"message": "no data yet"})
    with open(files[-1]) as f:
        data = json.load(f)
    return jsonify(data)


@app.route("/clear", methods=["POST"])
def clear_results():
    files = _list_result_files()
    for f in files:
        try:
            os.remove(f)
        except Exception:
            pass
    return redirect(url_for("index"))


@app.route("/results/<path:filename>")
def download_result(filename: str):
    os.makedirs("results", exist_ok=True)
    return send_from_directory("results", filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)


