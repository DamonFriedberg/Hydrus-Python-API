create_tables = """
    CREATE TABLE IF NOT EXISTS twitter_credentials (
        account_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        priority     INTEGER UNIQUE
                             NOT NULL,
        auth_token   TEXT    NOT NULL
                             CHECK (length(auth_token) == 40),
        csrf_token   TEXT    NOT NULL
                             CHECK (length(csrf_token) == 160),
        bearer_token TEXT    NOT NULL
                             CHECK (length(bearer_token) == 111 AND 
                                    bearer_token LIKE "Bearer %") 
    );

    CREATE TABLE IF NOT EXISTS twitter_rest_ids (
        display_name TEXT    PRIMARY KEY ON CONFLICT REPLACE,
        rest_id      INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS twitter_follows (
        follower INTEGER REFERENCES twitter_credentials (account_id) ON UPDATE CASCADE
                         NOT NULL,
        followed INTEGER NOT NULL,
        UNIQUE (
            follower,
            followed
        )
        ON CONFLICT IGNORE
    );

    CREATE TABLE IF NOT EXISTS twitter_blocks (
        blocked INTEGER REFERENCES twitter_credentials (account_id) ON UPDATE CASCADE
                        NOT NULL,
        blocker INTEGER NOT NULL,
        UNIQUE (
            blocked,
            blocker
        )
        ON CONFLICT IGNORE
    );

    CREATE TABLE IF NOT EXISTS twitter_privates (
        rest_id INTEGER PRIMARY KEY ON CONFLICT IGNORE
    );
"""

select_account_count = """
    SELECT
        count(1) as count
    FROM
        twitter_credentials
"""

select_rest_id = """
    SELECT
        rest_id
    FROM
        twitter_rest_ids
    WHERE
        display_name = ?
"""

select_all_accounts = """
    SELECT
        account_id,
        auth_token,
        csrf_token,
        bearer_token
    FROM 
        twitter_credentials
    ORDER BY 
        priority ASC
"""

select_accounts_for_creator = """
    SELECT
        account_id,
        auth_token,
        csrf_token,
        bearer_token,
        NOT EXISTS(
            SELECT
                1
            FROM
                twitter_blocks
            WHERE
                blocker = ?1
                and
                blocked = account_id
            )
        AND
        (
            NOT EXISTS(
                SELECT
                    1
                FROM
                    twitter_privates
                WHERE
                    rest_id = ?1
            )
            OR
            EXISTS(
                SELECT
                    1
                FROM
                    twitter_follows
                WHERE
                    followed = ?1
                    and
                    follower = account_id
            )
        ) as validity
    FROM
        twitter_credentials
    ORDER BY
        validity DESC,
        priority ASC
"""

select_only_valid_accounts_for_creator = """
    SELECT
        account_id,
        auth_token,
        csrf_token,
        bearer_token
    FROM
        twitter_credentials
    WHERE
        NOT EXISTS(
            SELECT
                1
            FROM
                twitter_blocks
            WHERE
                blocker = ?1
                and
                blocked = account_id
            )
        AND
        (
            NOT EXISTS(
                SELECT
                    1
                FROM
                    twitter_privates
                WHERE
                    rest_id = ?1
            )
            OR
            EXISTS(
                SELECT
                    1
                FROM
                    twitter_follows
                WHERE
                    followed = ?1
                    and
                    follower = account_id
            )
        )
    ORDER BY
        priority ASC
"""

insert_rest_id = """
    INSERT INTO
        twitter_rest_ids VALUES(?, ?)
"""

insert_follows = """
    INSERT INTO
        twitter_follows VALUES(?, ?)
"""

insert_blocks = """
    INSERT INTO
        twitter_blocks VALUES(?, ?)
"""

insert_privates = """
    INSERT INTO
        twitter_privates VALUES(?)
"""

delete_follows = """
    DELETE FROM
        twitter_follows
    WHERE
        follower = ?
        and
        followed = ?
"""

delete_blocks = """
    DELETE FROM
        twitter_blocks
    WHERE
        blocked = ?
        and
        blocker = ?
"""

delete_privates = """
    DELETE FROM
        twitter_privates
    WHERE
        rest_id = ?
"""