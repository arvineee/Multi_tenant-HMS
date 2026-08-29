from app import create_app

app = create_app()

if __name__ == "__main__":
    # DEBUG comes from config.py (FLASK_DEBUG env var / APP_ENV) — never
    # hardcode True here. Running with the interactive debugger enabled
    # in production lets anyone who can reach an error page execute
    # arbitrary code on the server.
    app.run(debug=app.config["DEBUG"], host=app.config.get("HOST", "127.0.0.1"), port=app.config.get("PORT", 5000))
