from flask import Flask
import twitter
import sys
import sqlite3
import argparse

class StatefulFlask(Flask):
    def __init__(self, name):
        super().__init__(name)
        self.state = {}
        self.log_file = open("log.txt", "a", buffering=1)

    def connect(self):
        con = sqlite3.connect("master.db", detect_types=sqlite3.PARSE_DECLTYPES)
        con.row_factory = sqlite3.Row
        return con

    def log(self, *objects, sep=' ', end='\n'):
        self.log_file.write(sep.join([str(o) for o in objects]) + end)

    def run(self, host=None, port=None, debug=None, load_dotenv=True, **options):
        if not self.debug or os.getenv("WERKZEUG_RUN_MAIN") == "true":
            with self.app_context():
                twitter.setup()
        super(StatefulFlask, self).run(host=host, port=port, debug=debug, load_dotenv=load_dotenv, **options)

app = StatefulFlask(__name__)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="Api Helper",
        description="A lightweight flask server that helps finnicky apis (just twitter at the moment) work nicely with hydrus"
    )
    subparsers = parser.add_subparsers(required=True, dest="command")
    
    parser_run = subparsers.add_parser("run", help="runs the server")
    parser_add = subparsers.add_parser("add", help="adds an account for the api to use")
    parser_list = subparsers.add_parser("list", help="lists accounts for a given service")
    parser_delete = subparsers.add_parser("del", help="removes an account from a given service")
    
    subparsers_add = parser_add.add_subparsers(required=True, dest="service")
    twitter_add = subparsers_add.add_parser("twitter", help="add a twitter account")
    twitter_add.add_argument("priority", help="an integer describing the priority of the account - the lowest is always used first")
    twitter_add.add_argument("auth_token", help="the auth_token cookie of the account")
    twitter_add.add_argument("csrf_token", help="the x-csrf-token header of the account")
    twitter_add.add_argument("bearer_token", help="the authorization header of the account")
    
    parser_list.add_argument("service", help="the service for which accounts should be listed")
    
    subparsers_del = parser_delete.add_subparsers(required=True, dest="service")
    twitter_del = subparsers_del.add_parser("twitter", help="remove a twitter account")
    twitter_del.add_argument("id", help="the id of the account to remove")
    
    args = parser.parse_args(sys.argv[1:])
    match args.command:
        case "run":
            try:
                app.run(debug=True)
            finally:
                app.log_file.close()
        case "add":
            match args.service:
                case "twitter":
                    with app.connect() as con:
                        con.execute("INSERT INTO twitter_credentials VALUES(NULL, ?, ?, ?, ?)", (args.priority, args.auth_token, args.csrf_token, args.bearer_token))
                        print("Insert successful.")
        case "list":
            match args.service:
                case "twitter":
                    with app.connect() as con:
                        res = con.execute("SELECT priority, auth_token, csrf_token, bearer_token FROM twitter_credentials ORDER BY priority ASC").fetchall()
                        print("Accounts for twitter:")
                        for row in res:
                            print(", ".join(f"{key}: {row[key]}" for key in row.keys()))
        case "del":
            match args.service:
                case "twitter":
                    with app.connect() as con:
                        con.execute("DELETE FROM twitter_credentials WHERE user_id = ?", (args.id))
                        print("Delete successful.")