# Exercise 2 - Environment setup and CRUD with PyMongo
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

Exercise 2 sets up MongoDB (local Docker and the Atlas cloud) and practises Create, Read, Update and Delete with PyMongo.

| Folder | What it does | Output |
|---|---|---|
| `Activity_2.1` | checks the Python version, the PyMongo driver, the local MongoDB, MongoDB Atlas (when `MONGO_URI_ATLAS` is set) and a live write/read/delete round trip | `environment_check.json` |
| `Activity_2.1_Try_it_yourself` | connects to Atlas and Docker MongoDB at the same time and compares their `server_info()` | `server_comparison.json` |
| `Activity_2.2` | a bookstore: inserts books with a nested publisher and a tags array; reads by year, genre, price range and tag; raises every price by 10 % with `$mul`; deletes one book | `books.json` |
| `Activity_2.2_Try_it_yourself` | increases the price of all books published before 2010 by 10 % and verifies the update | `books_2010.json` |

Connection strings are read from environment variables, never written in the code.

## Run
```bash
pip install pymongo
python exercises/exercise-02-setup-and-crud/Activity_2.1/activity_2_1_setup_environment.py
python exercises/exercise-02-setup-and-crud/Activity_2.2/activity_2_2_bookstore_crud.py
# the Atlas comparison needs both strings:
export MONGO_URI_DOCKER="mongodb://localhost:27017/"
export MONGO_URI_ATLAS="mongodb+srv://<user>:<password>@<cluster>.mongodb.net/"
python exercises/exercise-02-setup-and-crud/Activity_2.1_Try_it_yourself/activity_2_1_try_compare_servers.py
```
