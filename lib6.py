# lib4.py
import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
import datetime
import os

DB_NAME = "library.db"
DEFAULT_ISSUE_DAYS = 14


# ---------------------- DATABASE ----------------------
class Database:
    def __init__(self, path=DB_NAME):
        new_db = not os.path.exists(path)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.create_tables()
        self.migrate_schema()
        if new_db:
            self.ensure_admin()

    def create_tables(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT,
                role TEXT CHECK(role IN ('admin','user')) NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                author TEXT,
                genre TEXT,
                available_count INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                email TEXT UNIQUE,
                password TEXT,
                membership_type TEXT CHECK(membership_type IN ('regular','premium','student')),
                membership_expiry DATE
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                book_id INTEGER,
                issue_date TEXT,
                due_date TEXT,
                return_date TEXT,
                returned INTEGER DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(book_id) REFERENCES books(id)
            )
        """)
        self.conn.commit()

    def migrate_schema(self):
        # Safe migrations for older DBs
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(books)")
        cols = [r["name"] for r in cur.fetchall()]
        if "available_count" not in cols:
            cur.execute("ALTER TABLE books ADD COLUMN available_count INTEGER DEFAULT 0")
        # transactions: ensure due_date exists
        cur.execute("PRAGMA table_info(transactions)")
        tcols = [r["name"] for r in cur.fetchall()]
        if "due_date" not in tcols:
            cur.execute("ALTER TABLE transactions ADD COLUMN due_date TEXT")
        self.conn.commit()

    def ensure_admin(self):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users WHERE role='admin' LIMIT 1")
        if not cur.fetchone():
            cur.execute("INSERT INTO users (username,password,role) VALUES (?,?,?)", ("admin", "admin", "admin"))
            self.conn.commit()

    # --- Users ---
    def add_user(self, username, password, role="user"):
        cur = self.conn.cursor()
        try:
            cur.execute("INSERT INTO users (username,password,role) VALUES (?,?,?)", (username, password, role))
            self.conn.commit()
            return True, cur.lastrowid
        except sqlite3.IntegrityError:
            return False, "Username already exists"

    def authenticate(self, username, password):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
        return cur.fetchone()

    def get_user_by_username(self, username):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=?", (username,))
        return cur.fetchone()

    # --- Books ---
    def add_book(self, title, author, genre, available):
        cur = self.conn.cursor()
        cur.execute("INSERT INTO books (title,author,genre,available_count) VALUES (?,?,?,?)",
                    (title, author, genre, available))
        self.conn.commit()
        return cur.lastrowid

    def update_book(self, book_id, title, author, genre, available):
        cur = self.conn.cursor()
        cur.execute("UPDATE books SET title=?, author=?, genre=?, available_count=? WHERE id=?",
                    (title, author, genre, available, book_id))
        self.conn.commit()

    def delete_book(self, book_id):
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM transactions WHERE book_id=? AND returned=0", (book_id,))
        if cur.fetchone()[0] > 0:
            return False, "Cannot delete book with active issues"
        cur.execute("DELETE FROM books WHERE id=?", (book_id,))
        self.conn.commit()
        return True, None

    def list_books(self, search=None):
        cur = self.conn.cursor()
        if search:
            like = f"%{search}%"
            cur.execute("SELECT * FROM books WHERE title LIKE ? OR author LIKE ? OR genre LIKE ?",
                        (like, like, like))
        else:
            cur.execute("SELECT * FROM books")
        return cur.fetchall()

    def get_book(self, book_id):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM books WHERE id=?", (book_id,))
        return cur.fetchone()

    # --- Members ---
    def add_member(self, name, email, password, membership_type=None, expiry=None):
        cur = self.conn.cursor()
        try:
            cur.execute("INSERT INTO members (name,email,password,membership_type,membership_expiry) VALUES (?,?,?,?,?)",
                        (name, email, password, membership_type, expiry))
            self.conn.commit()
        except sqlite3.IntegrityError:
            return False, "Member email already exists"
        # create user account (username=email)
        self.add_user(email, password, role="user")
        return True, None

    def update_member(self, member_id, name, email, password, membership_type, expiry):
        cur = self.conn.cursor()
        # get old email to update users table if changed
        cur.execute("SELECT email FROM members WHERE id=?", (member_id,))
        old = cur.fetchone()
        old_email = old["email"] if old else None
        try:
            cur.execute("UPDATE members SET name=?, email=?, password=?, membership_type=?, membership_expiry=? WHERE id=?",
                        (name, email, password, membership_type, expiry, member_id))
            self.conn.commit()
        except sqlite3.IntegrityError:
            return False, "Email already used by another member"
        # sync users table
        if old_email and old_email != email:
            cur.execute("UPDATE users SET username=?, password=? WHERE username=?", (email, password, old_email))
        else:
            cur.execute("UPDATE users SET password=? WHERE username=?", (password, email))
        self.conn.commit()
        return True, None

    def delete_member(self, member_id):
        cur = self.conn.cursor()
        # prevent delete if active transactions
        cur.execute("SELECT COUNT(*) FROM transactions WHERE user_id=(SELECT id FROM users WHERE username=(SELECT email FROM members WHERE id=?)) AND returned=0", (member_id,))
        if cur.fetchone()[0] > 0:
            return False, "Member has active issued books"
        # delete member and corresponding user
        cur.execute("SELECT email FROM members WHERE id=?", (member_id,))
        r = cur.fetchone()
        if r and r["email"]:
            cur.execute("DELETE FROM users WHERE username=?", (r["email"],))
        cur.execute("DELETE FROM members WHERE id=?", (member_id,))
        self.conn.commit()
        return True, None

    def list_members(self):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM members")
        return cur.fetchall()

    def get_member(self, member_id):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM members WHERE id=?", (member_id,))
        return cur.fetchone()

    # --- Transactions ---
    def issue_book(self, username, book_id, days=DEFAULT_ISSUE_DAYS):
        # username = users.username (email)
        cur = self.conn.cursor()
        user = self.get_user_by_username(username)
        if not user:
            return False, "User not found"
        cur.execute("SELECT available_count FROM books WHERE id=?", (book_id,))
        b = cur.fetchone()
        if not b:
            return False, "Book not found"
        if b["available_count"] <= 0:
            return False, "No copies available"
        issue_date = datetime.date.today().isoformat()
        due_date = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
        cur.execute("INSERT INTO transactions (user_id,book_id,issue_date,due_date,returned) VALUES (?,?,?,?,0)",
                    (user["id"], book_id, issue_date, due_date))
        cur.execute("UPDATE books SET available_count=available_count-1 WHERE id=?", (book_id,))
        self.conn.commit()
        return True, None

    def return_book(self, trans_id):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM transactions WHERE id=?", (trans_id,))
        tx = cur.fetchone()
        if not tx:
            return False, "Transaction not found"
        if tx["returned"]:
            return False, "Already returned"
        return_date = datetime.date.today().isoformat()
        cur.execute("UPDATE transactions SET return_date=?, returned=1 WHERE id=?", (return_date, trans_id))
        cur.execute("UPDATE books SET available_count=available_count+1 WHERE id=?", (tx["book_id"],))
        self.conn.commit()
        return True, None

    def list_transactions(self, for_username=None):
        cur = self.conn.cursor()
        if for_username:
            cur.execute("SELECT t.*, u.username AS username, b.title AS book_title FROM transactions t "
                        "JOIN users u ON t.user_id=u.id JOIN books b ON t.book_id=b.id WHERE u.username=? ORDER BY t.issue_date DESC",
                        (for_username,))
        else:
            cur.execute("SELECT t.*, u.username AS username, b.title AS book_title FROM transactions t "
                        "JOIN users u ON t.user_id=u.id JOIN books b ON t.book_id=b.id ORDER BY t.issue_date DESC")
        return cur.fetchall()


# ---------------------- UI BASE ----------------------
class BasePage(tk.Frame):
    def __init__(self, parent, app, title):
        super().__init__(parent)
        self.app = app
        header = ttk.Label(self, text=title, font=("Segoe UI", 18, "bold"))
        header.pack(pady=12)


# ---------------------- APP ----------------------
class LibraryApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Library Management System")
        self.geometry("1024x700")
        self.resizable(False, False)
        self.db = Database()
        self.current_user = None

        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        # frames keyed by class for convenience
        self.frames = {}
        for Cls in (LoginPage, HomePage, ManageBooksPage, ManageMembersPage, TransactionsPage, ReportsPage):
            frm = Cls(container, self)
            self.frames[Cls] = frm
            frm.grid(row=0, column=0, sticky="nsew")

        self.show_frame(LoginPage)

    def show_frame(self, cls):
        frm = self.frames[cls]
        # call refresh if available
        if hasattr(frm, "refresh"):
            try:
                frm.refresh()
            except Exception:
                pass
        frm.tkraise()

    def login(self, username, password):
        row = self.db.authenticate(username, password)
        if row:
            self.current_user = {"id": row["id"], "username": row["username"], "role": row["role"]}
            self.show_frame(HomePage)
        else:
            messagebox.showerror("Login Failed", "Invalid credentials")


# ---------------------- PAGES ----------------------
class LoginPage(BasePage):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Login")
        box = ttk.LabelFrame(self, text="Sign in", padding=16)
        box.pack(pady=40)

        ttk.Label(box, text="Username:").grid(row=0, column=0, sticky="e", padx=8, pady=6)
        self.user_e = ttk.Entry(box, width=35)
        self.user_e.grid(row=0, column=1, padx=8, pady=6)

        ttk.Label(box, text="Password:").grid(row=1, column=0, sticky="e", padx=8, pady=6)
        self.pass_e = ttk.Entry(box, width=35, show="*")
        self.pass_e.grid(row=1, column=1, padx=8, pady=6)

        btn = ttk.Button(box, text="Login", command=self.on_login)
        btn.grid(row=2, column=0, columnspan=2, pady=10)

        ttk.Label(self, text="Default admin: admin / admin", foreground="gray").pack(pady=6)

    def on_login(self):
        u = self.user_e.get().strip()
        p = self.pass_e.get().strip()
        if not u or not p:
            messagebox.showwarning("Required", "Enter username and password")
            return
        self.app.login(u, p)

    def refresh(self):
        self.user_e.delete(0, "end"); self.pass_e.delete(0, "end")


class HomePage(BasePage):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Dashboard")
        frame = ttk.Frame(self)
        frame.pack(pady=20)

        self.books_btn = ttk.Button(frame, text="Manage Books", width=20, command=lambda: app.show_frame(ManageBooksPage))
        self.members_btn = ttk.Button(frame, text="Manage Members", width=20, command=lambda: app.show_frame(ManageMembersPage))
        self.trans_btn = ttk.Button(frame, text="Transactions", width=20, command=lambda: app.show_frame(TransactionsPage))
        self.reports_btn = ttk.Button(frame, text="Reports", width=20, command=lambda: app.show_frame(ReportsPage))
        self.logout_btn = ttk.Button(frame, text="Logout", width=20, command=self.logout)

        self.books_btn.grid(row=0, column=0, padx=12, pady=8)
        self.members_btn.grid(row=0, column=1, padx=12, pady=8)
        self.trans_btn.grid(row=1, column=0, padx=12, pady=8)
        self.reports_btn.grid(row=1, column=1, padx=12, pady=8)
        self.logout_btn.grid(row=2, column=0, columnspan=2, pady=14)

    def refresh(self):
        if not self.app.current_user:
            return
        role = self.app.current_user["role"]
        if role != "admin":
            self.books_btn.state(["disabled"])
            self.members_btn.state(["disabled"])
        else:
            self.books_btn.state(["!disabled"])
            self.members_btn.state(["!disabled"])

    def logout(self):
        self.app.current_user = None
        self.app.show_frame(LoginPage)


class ManageBooksPage(BasePage):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Manage Books")
        # Search
        top = ttk.Frame(self)
        top.pack(fill="x", padx=12)
        ttk.Label(top, text="Search:").pack(side="left", padx=6)
        self.search_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.search_var, width=40).pack(side="left")
        ttk.Button(top, text="Search", command=self.refresh).pack(side="left", padx=6)
        ttk.Button(top, text="Clear", command=self.clear_search).pack(side="left")

        # Form
        form = ttk.LabelFrame(self, text="Book Details", padding=10)
        form.pack(fill="x", padx=12, pady=8)
        ttk.Label(form, text="Title:").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        self.title_e = ttk.Entry(form, width=40); self.title_e.grid(row=0, column=1, padx=6, pady=4)
        ttk.Label(form, text="Author:").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        self.author_e = ttk.Entry(form, width=40); self.author_e.grid(row=1, column=1, padx=6, pady=4)
        ttk.Label(form, text="Genre:").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        self.genre_e = ttk.Entry(form, width=40); self.genre_e.grid(row=2, column=1, padx=6, pady=4)
        ttk.Label(form, text="Available Count:").grid(row=3, column=0, sticky="e", padx=6, pady=4)
        self.available_e = ttk.Entry(form, width=20); self.available_e.grid(row=3, column=1, sticky="w", padx=6, pady=4)

        btnf = ttk.Frame(form); btnf.grid(row=4, column=0, columnspan=2, pady=8)
        self.add_b = ttk.Button(btnf, text="Add", command=self.add_book); self.add_b.pack(side="left", padx=6)
        self.upd_b = ttk.Button(btnf, text="Update", command=self.update_book); self.upd_b.pack(side="left", padx=6)
        self.del_b = ttk.Button(btnf, text="Delete", command=self.delete_book); self.del_b.pack(side="left", padx=6)

        # Table
        cols = ("id", "title", "author", "genre", "available_count")
        self.tree = ttk.Treeview(self, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=c.replace("_", " ").title())
            self.tree.column(c, width=180 if c != "id" else 60, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=12, pady=8)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_book)

        bottom = ttk.Frame(self); bottom.pack(fill="x", padx=12)
        ttk.Button(bottom, text="Back", command=lambda: app.show_frame(HomePage)).pack(side="right", pady=6)

    def clear_search(self):
        self.search_var.set("")
        self.refresh()

    def refresh(self):
        # enable/disable admin-only controls
        if not self.app.current_user:
            return
        role = self.app.current_user["role"]
        if role != "admin":
            self.add_b.state(["disabled"]); self.upd_b.state(["disabled"]); self.del_b.state(["disabled"])
        else:
            self.add_b.state(["!disabled"]); self.upd_b.state(["!disabled"]); self.del_b.state(["!disabled"])

        for r in self.tree.get_children():
            self.tree.delete(r)
        rows = self.app.db.list_books(self.search_var.get().strip() or None)
        for r in rows:
            self.tree.insert("", "end", values=(r["id"], r["title"], r["author"], r["genre"], r["available_count"]))

        # clear form
        self.title_e.delete(0, "end"); self.author_e.delete(0, "end"); self.genre_e.delete(0, "end"); self.available_e.delete(0, "end")

    def on_select_book(self, _):
        sel = self.tree.selection()
        if not sel: return
        vals = self.tree.item(sel[0])["values"]
        self.title_e.delete(0, "end"); self.title_e.insert(0, vals[1])
        self.author_e.delete(0, "end"); self.author_e.insert(0, vals[2])
        self.genre_e.delete(0, "end"); self.genre_e.insert(0, vals[3])
        self.available_e.delete(0, "end"); self.available_e.insert(0, vals[4])

    def add_book(self):
        if self.app.current_user["role"] != "admin":
            messagebox.showwarning("Permission", "Only admin can add books"); return
        title = self.title_e.get().strip()
        if not title:
            messagebox.showwarning("Required", "Title is required"); return
        try:
            avail = int(self.available_e.get().strip() or 0)
        except ValueError:
            messagebox.showwarning("Invalid", "Available count must be integer"); return
        self.app.db.add_book(title, self.author_e.get().strip(), self.genre_e.get().strip(), avail)
        messagebox.showinfo("Added", "Book added")
        self.refresh()

    def update_book(self):
        if self.app.current_user["role"] != "admin":
            messagebox.showwarning("Permission", "Only admin"); return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a book"); return
        book_id = self.tree.item(sel[0])["values"][0]
        try:
            avail = int(self.available_e.get().strip() or 0)
        except ValueError:
            messagebox.showwarning("Invalid", "Available count must be integer"); return
        self.app.db.update_book(book_id, self.title_e.get().strip(), self.author_e.get().strip(), self.genre_e.get().strip(), avail)
        messagebox.showinfo("Updated", "Book updated")
        self.refresh()

    def delete_book(self):
        if self.app.current_user["role"] != "admin":
            messagebox.showwarning("Permission", "Only admin"); return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a book"); return
        book_id = self.tree.item(sel[0])["values"][0]
        ok, msg = self.app.db.delete_book(book_id)
        if not ok:
            messagebox.showerror("Cannot delete", msg)
        else:
            messagebox.showinfo("Deleted", "Book deleted")
        self.refresh()


class ManageMembersPage(BasePage):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Manage Members")
        form = ttk.LabelFrame(self, text="Member Details", padding=10)
        form.pack(fill="x", padx=12, pady=8)

        ttk.Label(form, text="Name:").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        self.name_e = ttk.Entry(form, width=40); self.name_e.grid(row=0, column=1, padx=6, pady=4)
        ttk.Label(form, text="Email:").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        self.email_e = ttk.Entry(form, width=40); self.email_e.grid(row=1, column=1, padx=6, pady=4)
        ttk.Label(form, text="Password:").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        self.pw_e = ttk.Entry(form, width=40); self.pw_e.grid(row=2, column=1, padx=6, pady=4)
        ttk.Label(form, text="Type:").grid(row=3, column=0, sticky="e", padx=6, pady=4)
        self.type_cb = ttk.Combobox(form, values=["regular", "premium", "student"], state="readonly"); self.type_cb.grid(row=3, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(form, text="Expiry (YYYY-MM-DD):").grid(row=4, column=0, sticky="e", padx=6, pady=4)
        self.expiry_e = ttk.Entry(form, width=40); self.expiry_e.grid(row=4, column=1, padx=6, pady=4)

        btnf = ttk.Frame(form); btnf.grid(row=5, column=0, columnspan=2, pady=8)
        self.add_m = ttk.Button(btnf, text="Add", command=self.add_member); self.add_m.pack(side="left", padx=6)
        self.upd_m = ttk.Button(btnf, text="Update", command=self.update_member); self.upd_m.pack(side="left", padx=6)
        self.del_m = ttk.Button(btnf, text="Delete", command=self.delete_member); self.del_m.pack(side="left", padx=6)

        cols = ("id", "name", "email", "type", "expiry")
        self.tree = ttk.Treeview(self, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=c.replace("_", " ").title())
            self.tree.column(c, width=180, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=12, pady=8)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_member)

        ttk.Button(self, text="Back", command=lambda: app.show_frame(HomePage)).pack(pady=6)

    def refresh(self):
        # admin-only controls
        if not self.app.current_user:
            return
        if self.app.current_user["role"] != "admin":
            self.add_m.state(["disabled"]); self.upd_m.state(["disabled"]); self.del_m.state(["disabled"])
        else:
            self.add_m.state(["!disabled"]); self.upd_m.state(["!disabled"]); self.del_m.state(["!disabled"])

        for r in self.tree.get_children():
            self.tree.delete(r)
        rows = self.app.db.list_members()
        for r in rows:
            self.tree.insert("", "end", values=(r["id"], r["name"], r["email"], r["membership_type"], r["membership_expiry"]))
        # clear fields
        self.name_e.delete(0, "end"); self.email_e.delete(0, "end"); self.pw_e.delete(0, "end"); self.type_cb.set(""); self.expiry_e.delete(0, "end")

    def add_member(self):
        if self.app.current_user["role"] != "admin":
            messagebox.showwarning("Permission", "Only admin can add members"); return
        name = self.name_e.get().strip()
        email = self.email_e.get().strip()
        pw = self.pw_e.get().strip()
        mtype = self.type_cb.get().strip() or None
        expiry = self.expiry_e.get().strip() or None
        if not name or not email or not pw:
            messagebox.showwarning("Required", "Name, Email and Password required"); return
        ok, msg = self.app.db.add_member(name, email, pw, mtype, expiry)
        if not ok:
            messagebox.showerror("Error", msg)
        else:
            messagebox.showinfo("Added", "Member (and user) created")
        self.refresh()

    def on_select_member(self, _):
        sel = self.tree.selection()
        if not sel: return
        vals = self.tree.item(sel[0])["values"]
        self.name_e.delete(0, "end"); self.name_e.insert(0, vals[1])
        self.email_e.delete(0, "end"); self.email_e.insert(0, vals[2])
        self.pw_e.delete(0, "end")
        self.type_cb.set(vals[3] or "")
        self.expiry_e.delete(0, "end"); self.expiry_e.insert(0, vals[4] or "")

    def update_member(self):
        if self.app.current_user["role"] != "admin":
            messagebox.showwarning("Permission", "Only admin"); return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a member"); return
        mem_id = self.tree.item(sel[0])["values"][0]
        name = self.name_e.get().strip(); email = self.email_e.get().strip(); pw = self.pw_e.get().strip()
        mtype = self.type_cb.get().strip() or None; expiry = self.expiry_e.get().strip() or None
        if not name or not email:
            messagebox.showwarning("Required", "Name and Email required"); return
        ok, msg = self.app.db.update_member(mem_id, name, email, pw, mtype, expiry)
        if not ok:
            messagebox.showerror("Error", msg)
        else:
            messagebox.showinfo("Updated", "Member updated")
        self.refresh()

    def delete_member(self):
        if self.app.current_user["role"] != "admin":
            messagebox.showwarning("Permission", "Only admin"); return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a member"); return
        mem_id = self.tree.item(sel[0])["values"][0]
        ok, msg = self.app.db.delete_member(mem_id)
        if not ok:
            messagebox.showerror("Cannot delete", msg)
        else:
            messagebox.showinfo("Deleted", "Member deleted")
        self.refresh()


class TransactionsPage(BasePage):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Transactions")
        form = ttk.LabelFrame(self, text="Issue / Return", padding=10)
        form.pack(fill="x", padx=12, pady=8)

        ttk.Label(form, text="Member Email (username):").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        self.member_e = ttk.Entry(form, width=40); self.member_e.grid(row=0, column=1, padx=6, pady=4)
        ttk.Label(form, text="Book ID:").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        self.bookid_e = ttk.Entry(form, width=20); self.bookid_e.grid(row=1, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(form, text="Days (optional):").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        self.days_e = ttk.Entry(form, width=10); self.days_e.grid(row=2, column=1, sticky="w", padx=6, pady=4)
        self.days_e.insert(0, str(DEFAULT_ISSUE_DAYS))

        btnf = ttk.Frame(form); btnf.grid(row=3, column=0, columnspan=2, pady=8)
        ttk.Button(btnf, text="Issue Book", command=self.issue_book).pack(side="left", padx=6)
        ttk.Button(btnf, text="Return Selected", command=self.return_selected).pack(side="left", padx=6)

        cols = ("id", "username", "book_title", "issue_date", "due_date", "return_date", "returned")
        self.tree = ttk.Treeview(self, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=c.replace("_", " ").title())
            self.tree.column(c, width=130, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=12, pady=8)

        ttk.Button(self, text="Back", command=lambda: app.show_frame(HomePage)).pack(pady=6)

    def refresh(self):
        for r in self.tree.get_children():
            self.tree.delete(r)
        # admin sees all, user sees own
        if not self.app.current_user:
            return
        if self.app.current_user["role"] == "admin":
            rows = self.app.db.list_transactions()
        else:
            rows = self.app.db.list_transactions(for_username=self.app.current_user["username"])
        for r in rows:
            self.tree.insert("", "end", values=(r["id"], r["username"], r["book_title"], r["issue_date"], r["due_date"], r["return_date"] or "", r["returned"]))

    def issue_book(self):
        username = self.member_e.get().strip()
        try:
            book_id = int(self.bookid_e.get().strip())
        except Exception:
            messagebox.showwarning("Invalid", "Book ID must be integer"); return
        try:
            days = int(self.days_e.get().strip())
        except Exception:
            days = DEFAULT_ISSUE_DAYS
        ok, msg = self.app.db.issue_book(username, book_id, days)
        if not ok:
            messagebox.showerror("Error", msg)
        else:
            messagebox.showinfo("Issued", "Book issued")
        self.refresh()

    def return_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a transaction to return"); return
        tx_id = self.tree.item(sel[0])["values"][0]
        ok, msg = self.app.db.return_book(tx_id)
        if not ok:
            messagebox.showerror("Error", msg)
        else:
            messagebox.showinfo("Returned", "Book return processed")
        self.refresh()


class ReportsPage(BasePage):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Reports")
        ttk.Label(self, text="Transactions Report", font=("Segoe UI", 12)).pack(pady=6)
        cols = ("id", "username", "book_title", "issue_date", "due_date", "return_date", "returned")
        self.tree = ttk.Treeview(self, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=c.replace("_", " ").title())
            self.tree.column(c, width=130, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=12, pady=8)
        ttk.Button(self, text="Back", command=lambda: app.show_frame(HomePage)).pack(pady=6)

    def refresh(self):
        for r in self.tree.get_children():
            self.tree.delete(r)
        if not self.app.current_user:
            return
        if self.app.current_user["role"] == "admin":
            rows = self.app.db.list_transactions()
        else:
            rows = self.app.db.list_transactions(for_username=self.app.current_user["username"])
        for r in rows:
            self.tree.insert("", "end", values=(r["id"], r["username"], r["book_title"], r["issue_date"], r["due_date"], r["return_date"] or "", r["returned"]))


# ---------------------- MAIN ----------------------
def main():
    app = LibraryApp()
    app.mainloop()


if __name__ == "__main__":
    main()
