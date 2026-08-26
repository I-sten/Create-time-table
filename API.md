# Automatic Timetable Solver API

## Start the server with PM2

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm install --global pm2
pm2 start ecosystem.config.js
```

Open `http://localhost:8000/docs` for interactive Swagger documentation or
`http://localhost:8000/openapi.json` for the machine-readable API contract.

Useful process-management commands:

```powershell
pm2 status
pm2 logs timetable-api
pm2 restart timetable-api
pm2 stop timetable-api
pm2 save
```

On Linux, create the virtual environment and install dependencies with
`python3 -m venv .venv` and `.venv/bin/python -m pip install -r requirements.txt`.
The same PM2 configuration works on Windows and Linux.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service information |
| GET | `/api/v1/health` | Health check |
| POST | `/api/v1/timetables/validate` | Validate input without solving |
| POST | `/api/v1/timetables/generate` | Generate and verify a JSON timetable |
| POST | `/api/v1/timetables/generate.csv` | Generate and download a CSV timetable |

All POST endpoints accept the configuration structure below directly as their
JSON request body. Periods and days may be referenced in rules by number or name.

```json
{
  "school": {
    "name": "Example School",
    "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
    "periods": [
      {"number": 1, "name": "Period 1", "start": "08:00", "end": "09:00"},
      {"number": 2, "name": "Period 2", "start": "09:00", "end": "10:00"}
    ],
    "breaks": [{"after_period": 1, "name": "Break"}]
  },
  "teachers": [{"id": "T001", "name": "Teacher One"}],
  "classes": [
    {
      "id": "C1",
      "name": "Class 1",
      "subjects": [
        {
          "subject": "Mathematics",
          "teacher": "T001",
          "sessions_per_week": 2,
          "max_sessions_per_day": 1
        }
      ]
    }
  ],
  "rules": {
    "teacher_unavailable": [{"teacher": "T001", "day": "Friday", "period": 2}],
    "class_unavailable": [{"class": "C1", "day": "Monday", "period": 2}],
    "fixed_lessons": [
      {"class": "C1", "subject": "Mathematics", "teacher": "T001", "day": "Tuesday", "period": 1, "session": 1}
    ],
    "free_period_restrictions": [
      {"class": "C1", "allowed_days": ["Friday"], "allowed_period_positions": ["last"]}
    ],
    "subject_position_preferences": [
      {"class": "C1", "subject": "Mathematics", "when": "first", "weight": 50}
    ],
    "max_class_consecutive_periods": 3,
    "max_teacher_consecutive_periods": 3,
    "spread_subject_across_days": true
  }
}
```

Successful validation returns:

```json
{"valid": true, "total_lessons": 2, "message": "Configuration is structurally valid."}
```

Generation returns school metadata, normalized days and periods, a `timetable`
array, and solver statistics. Each timetable row contains `lesson_id`, `class_id`,
`class`, `subject`, `teacher`, `session`, `day`, `day_index`, `period`,
`period_name`, `start`, and `end`.

Errors use standard HTTP codes: `422` for invalid data, `409` for valid but
infeasible constraints, and `500` for an unexpected server failure. Error bodies
have the form `{"detail": "message"}`.
