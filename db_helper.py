"""
Database Helper Module for Connect Link Properties POS System
Provides context manager for safe database connection handling
"""

from contextlib import contextmanager
import psycopg2
import os

# Get database URL from environment or fallback to default
external_database_url = os.getenv(
    'DATABASE_URL',
    "postgresql://connectlinkdata_user:RsYLVxq6lzCBXV7m3e2drdiNMebYBFIC@dpg-d4m0bqggjchc73avg3eg-a.oregon-postgres.render.com/connectlinkdata"
)

@contextmanager
def get_db():
    """
    Context manager for database connections.
    
    Usage:
        with get_db() as (cursor, connection):
            cursor.execute("SELECT * FROM table")
            result = cursor.fetchone()
            connection.commit()
    """
    connection = None
    cursor = None
    try:
        connection = psycopg2.connect(external_database_url)
        cursor = connection.cursor()
        yield cursor, connection
    except Exception as e:
        if connection:
            connection.rollback()
        raise e
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if connection:
            try:
                connection.close()
            except Exception:
                pass


@contextmanager
def get_db_cursor_only():
    """
    Simplified context manager that yields only cursor.
    
    Usage:
        with get_db_cursor_only() as cursor:
            cursor.execute("SELECT * FROM table")
            results = cursor.fetchall()
    """
    connection = None
    cursor = None
    try:
        connection = psycopg2.connect(external_database_url)
        cursor = connection.cursor()
        yield cursor
    except Exception as e:
        if connection:
            connection.rollback()
        raise e
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if connection:
            try:
                connection.close()
            except Exception:
                pass


def execute_query(query, params=None, fetch_one=False, fetch_all=False, commit=False):
    """
    Helper function for single queries.
    
    Args:
        query: SQL query string
        params: Tuple of parameters for the query
        fetch_one: If True, returns one result
        fetch_all: If True, returns all results
        commit: If True, commits the transaction
    
    Returns:
        Query result or None
    """
    with get_db() as (cursor, connection):
        cursor.execute(query, params or ())
        
        if fetch_one:
            result = cursor.fetchone()
        elif fetch_all:
            result = cursor.fetchall()
        else:
            result = None
        
        if commit:
            connection.commit()
        
        return result