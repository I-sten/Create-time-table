import json
import csv
import os
from collections import defaultdict
from ortools.sat.python import cp_model

DATA_FILE = "school_data.json"
OUTPUT_JSON = "timetable_output.json"
OUTPUT_CSV = "timetable.csv"


def load_data():
    if not os.path.exists(DATA_FILE):
        raise FileNotFoundError(f"Could not find {DATA_FILE}")
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_days(days):
    if not days:
        return []
    normalized = []
    seen = set()
    for item in days:
        if isinstance(item, dict):
            if "name" not in item:
                raise ValueError("Every day object must include a 'name'.")
            name = str(item["name"]).strip()
        else:
            name = str(item).strip()
        if not name:
            raise ValueError("Day names must be non-empty.")
        if name.lower() in seen:
            raise ValueError(f"Duplicate day name: {name}")
        seen.add(name.lower())
        normalized.append(name)
    return normalized


def normalize_periods(periods):
    if not periods:
        return []
    normalized = []
    seen_numbers = set()
    seen_names = set()
    for item in periods:
        if isinstance(item, dict):
            if "number" not in item:
                raise ValueError("Every period object must include a 'number'.")
            number = item["number"]
            name = item.get("name", number)
        else:
            number = item
            name = item
        number = int(number) if isinstance(number, (int, str)) and str(number).isdigit() else number
        name = str(name).strip()
        if not name:
            raise ValueError("Period names must be non-empty.")
        if number in seen_numbers:
            raise ValueError(f"Duplicate period number: {number}")
        if name.lower() in seen_names:
            raise ValueError(f"Duplicate period name: {name}")
        seen_numbers.add(number)
        seen_names.add(name.lower())
        entry = item.copy() if isinstance(item, dict) else {}
        entry["number"] = number
        entry["name"] = name
        normalized.append(entry)
    return normalized


def normalize_breaks(breaks, periods):
    if not breaks:
        return []
    normalized = []
    seen = set()
    for item in breaks:
        if not isinstance(item, dict):
            raise ValueError("Every break must be an object.")
        if "after_period" not in item:
            raise ValueError("Every break object must include after_period.")
        after = item["after_period"]
        if isinstance(after, str) and after.isdigit():
            after = int(after)
        idx = period_index(periods, after)
        if idx == len(periods) - 1:
            raise ValueError("Break after the last period is not allowed.")
        if idx in seen:
            raise ValueError(f"Duplicate break after period: {after}")
        seen.add(idx)
        normalized.append({
            "after_period": after,
            "name": str(item.get("name", "Break")).strip(),
        })
    return normalized


def validate_data(data):
    school = data.get("school", {})
    days = normalize_days(school.get("days", []))
    periods = normalize_periods(school.get("periods", []))
    school["days"] = days
    school["periods"] = periods
    if not days:
        raise ValueError("No school days defined.")
    if not periods:
        raise ValueError("No periods defined.")

    teacher_ids = {t["id"] for t in data.get("teachers", [])}
    classes = data.get("classes", [])
    class_ids = {c["id"] for c in classes}
    if not classes:
        raise ValueError("No classes defined.")

    breaks = normalize_breaks(school.get("breaks", []), periods)
    school["breaks"] = breaks
    period_groups = build_contiguous_period_groups(periods, breaks)

    for pref in data.get("rules", {}).get("subject_position_preferences", []):
        if not isinstance(pref, dict):
            raise ValueError("Each subject_position_preferences entry must be an object.")
        if "class" not in pref or "subject" not in pref or "when" not in pref:
            raise ValueError("subject_position_preferences entries require class, subject, and when.")
        if pref["class"] not in class_ids:
            raise ValueError(f"Unknown class in subject_position_preferences: {pref['class']}")
        if str(pref["when"]).strip().lower() not in {"first", "last"}:
            raise ValueError(f"Invalid when value in subject_position_preferences: {pref['when']}")

    for restriction in data.get("rules", {}).get("free_period_restrictions", []):
        if not isinstance(restriction, dict):
            raise ValueError("Each free_period_restrictions entry must be an object.")
        if "class" not in restriction:
            raise ValueError("free_period_restrictions entries require class.")
        if restriction["class"] not in class_ids:
            raise ValueError(f"Unknown class in free_period_restrictions: {restriction['class']}")
        if "allowed_days" in restriction:
            if not isinstance(restriction["allowed_days"], list):
                raise ValueError("allowed_days must be a list in free_period_restrictions.")
            for day in restriction["allowed_days"]:
                day_index(days, day)
        if "allowed_period_positions" in restriction:
            if not isinstance(restriction["allowed_period_positions"], list):
                raise ValueError("allowed_period_positions must be a list in free_period_restrictions.")
            for position in restriction["allowed_period_positions"]:
                norm = str(position).strip().lower()
                if norm not in {"first", "last"} and not str(position).isdigit():
                    period_index(periods, position)

    breaks = normalize_breaks(school.get("breaks", []), periods)
    school["breaks"] = breaks

    for cls in classes:
        total = 0
        seen_subjects = set()
        for s in cls.get("subjects", []):
            subject = s["subject"]
            teacher = s["teacher"]
            sessions = int(s["sessions_per_week"])
            max_day = int(s.get("max_sessions_per_day", sessions))
            if teacher not in teacher_ids:
                raise ValueError(f"Unknown teacher {teacher} in {cls['name']} / {subject}.")
            if sessions < 0:
                raise ValueError(f"Negative sessions_per_week: {cls['name']} / {subject}.")
            if max_day < 1:
                raise ValueError(f"max_sessions_per_day must be >= 1: {cls['name']} / {subject}.")
            if subject in seen_subjects:
                raise ValueError(f"Duplicate subject entry in {cls['name']}: {subject}.")
            seen_subjects.add(subject)
            total += sessions
            if sessions > len(days) * max_day:
                raise ValueError(
                    f"Impossible subject rule: {cls['name']} / {subject}: "
                    f"{sessions} sessions/week but max {max_day}/day over {len(days)} days "
                    f"allows only {len(days) * max_day}."
                )
        capacity = len(days) * len(periods)
        if total > capacity:
            raise ValueError(
                f"Impossible class capacity: {cls['name']} requires {total} lessons "
                f"but has only {capacity} class slots."
            )
        consecutive_limit = data.get("rules", {}).get("max_class_consecutive_periods")
        if consecutive_limit is not None:
            max_possible = max_slots_with_consecutive_limit(len(periods), consecutive_limit)
            if total > len(days) * max_possible:
                raise ValueError(
                    f"Impossible class consecutive rule: {cls['name']} needs {total} lessons "
                    f"but with max {consecutive_limit} consecutive periods and {len(periods)} periods/day, "
                    f"each day can host at most {max_possible} lessons."
                )

    print("Data validation successful.")


def day_index(days, name):
    if isinstance(name, int):
        if 1 <= name <= len(days):
            return name - 1
    if isinstance(name, str) and name.isdigit():
        i = int(name) - 1
        if 0 <= i < len(days):
            return i
    normalized = str(name).strip().lower()
    for idx, day in enumerate(days):
        if str(day).strip().lower() == normalized:
            return idx
    raise ValueError(f"Unknown day: {name}")


def period_index(periods, identifier):
    if isinstance(identifier, int):
        for idx, period in enumerate(periods):
            if period["number"] == identifier:
                return idx
    normalized = str(identifier).strip()
    if normalized.isdigit():
        i = int(normalized)
        for idx, period in enumerate(periods):
            if period["number"] == i:
                return idx
    for idx, period in enumerate(periods):
        if str(period["name"]).strip().lower() == normalized.lower():
            return idx
        if str(period["number"]).strip().lower() == normalized.lower():
            return idx
    raise ValueError(f"Invalid period identifier: {identifier}")


def build_contiguous_period_groups(periods, breaks):
    if not breaks:
        return [list(range(len(periods)))]
    break_indices = {period_index(periods, item["after_period"]) for item in breaks}
    groups = []
    current = []
    for idx in range(len(periods)):
        current.append(idx)
        if idx in break_indices:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def period_positions(period_groups, when):
    if when == "first":
        return {group[0] for group in period_groups}
    if when == "last":
        return {group[-1] for group in period_groups}
    return set()


def max_slots_with_consecutive_limit(num_periods, limit):
    if limit is None:
        return num_periods
    limit = int(limit)
    if limit >= num_periods:
        return num_periods
    if limit < 1:
        return 0
    return num_periods - (num_periods // (limit + 1))


def build_lessons(data):
    lessons = []
    for cls in data["classes"]:
        for info in cls["subjects"]:
            for n in range(int(info["sessions_per_week"])):
                lessons.append({
                    "id": f"{cls['id']}_{info['subject']}_{n + 1}",
                    "class_id": cls["id"],
                    "class_name": cls["name"],
                    "subject": info["subject"],
                    "teacher": info["teacher"],
                    "session_number": n + 1,
                    "max_sessions_per_day": int(info.get("max_sessions_per_day", info["sessions_per_week"])),
                })
    return lessons


def diagnostic_report(data, lessons):
    school = data["school"]
    days = school["days"]
    periods = school["periods"]
    rules = data.get("rules", {})
    issues = []

    print("\n========== FEASIBILITY DIAGNOSTIC ==========")
    print(f"School capacity per class: {len(days)} days x {len(periods)} periods = {len(days) * len(periods)}")

    # Class capacity and subject daily limits.
    by_class = defaultdict(list)
    for i, l in enumerate(lessons):
        by_class[l["class_id"]].append(i)

    for cls in data["classes"]:
        total = sum(int(s["sessions_per_week"]) for s in cls["subjects"])
        if total > len(days) * len(periods):
            issues.append(f"CLASS CAPACITY: {cls['name']} needs {total}, only {len(days)*len(periods)} slots.")
        for s in cls["subjects"]:
            n = int(s["sessions_per_week"])
            m = int(s.get("max_sessions_per_day", n))
            if n > len(days) * m:
                issues.append(
                    f"SUBJECT DAILY LIMIT: {cls['name']} / {s['subject']} needs {n}, "
                    f"but max {m}/day over {len(days)} days allows {len(days)*m}."
                )

    # Teacher availability capacity.
    unavailable_teacher = set()
    for item in rules.get("teacher_unavailable", []):
        d = day_index(days, item["day"])
        p = period_index(periods, item["period"])
        unavailable_teacher.add((item["teacher"], d, p))

    teacher_required = defaultdict(int)
    teacher_unavailable_count = defaultdict(set)
    for t, d, p in unavailable_teacher:
        teacher_unavailable_count[t].add((d, p))
    for l in lessons:
        teacher_required[l["teacher"]] += 1

    for teacher, required in sorted(teacher_required.items()):
        available = len(days) * len(periods) - len(teacher_unavailable_count[teacher])
        if required > available:
            issues.append(f"TEACHER CAPACITY: {teacher} needs {required} lessons but has only {available} available slots.")

    # Class unavailable capacity.
    unavailable_class = defaultdict(set)
    for item in rules.get("class_unavailable", []):
        unavailable_class[item["class"]].add((day_index(days, item["day"]), period_index(periods, item["period"])))
    for class_id, inds in by_class.items():
        available = len(days) * len(periods) - len(unavailable_class[class_id])
        if len(inds) > available:
            issues.append(f"CLASS AVAILABILITY: {class_id} needs {len(inds)} lessons but has only {available} available slots.")

    # Fixed lesson sanity.
    for fixed in rules.get("fixed_lessons", []):
        matches = [i for i, l in enumerate(lessons)
                   if l["class_id"] == fixed["class"] and l["subject"] == fixed["subject"] and l["teacher"] == fixed["teacher"]]
        if not matches:
            issues.append(f"FIXED LESSON: {fixed['class']} / {fixed['subject']} / {fixed['teacher']} does not exist.")

    # Same fixed slot used by multiple classes is fine, but same teacher/class is not.
    fixed_slots = defaultdict(list)
    for fixed in rules.get("fixed_lessons", []):
        d = day_index(days, fixed["day"])
        p = period_index(periods, fixed["period"])
        key = (d, p)
        fixed_slots[key].append(fixed)
    for slot, items in fixed_slots.items():
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                if items[a]["class"] == items[b]["class"]:
                    issues.append(f"FIXED CONFLICT: class {items[a]['class']} has multiple fixed lessons at day {slot[0]+1}, period {slot[1]+1}.")
                if items[a]["teacher"] == items[b]["teacher"]:
                    issues.append(f"FIXED CONFLICT: teacher {items[a]['teacher']} has multiple fixed lessons at day {slot[0]+1}, period {slot[1]+1}.")

    # Consecutive limits cannot make a class/teacher impossible by themselves unless all slots are occupied.
    max_class_consecutive = rules.get("max_class_consecutive_periods")
    if max_class_consecutive and max_class_consecutive < 1:
        issues.append("RULE: max_class_consecutive_periods must be >= 1.")
    max_teacher_consecutive = rules.get("max_teacher_consecutive_periods")
    if max_teacher_consecutive and max_teacher_consecutive < 1:
        issues.append("RULE: max_teacher_consecutive_periods must be >= 1.")

    if issues:
        print("\nERROR: Direct data conflicts found:")
        for issue in issues:
            print("  -", issue)
    else:
        print("OK: No direct capacity/rule contradiction detected.")
    return issues


def make_model(data, lessons, include_objective=True):
    school = data["school"]
    days = school["days"]
    periods = school["periods"]
    breaks = school.get("breaks", [])
    period_groups = build_contiguous_period_groups(periods, breaks)
    nd, np = len(days), len(periods)
    rules = data.get("rules", {})

    model = cp_model.CpModel()
    x = {}
    for i in range(len(lessons)):
        for d in range(nd):
            for p in range(np):
                x[i, d, p] = model.new_bool_var(f"x_{i}_{d}_{p}")

    # Exactly once per lesson.
    for i in range(len(lessons)):
        model.add_exactly_one(x[i, d, p] for d in range(nd) for p in range(np))

    class_groups = defaultdict(list)
    teacher_groups = defaultdict(list)
    subject_groups = defaultdict(list)
    for i, l in enumerate(lessons):
        class_groups[l["class_id"]].append(i)
        teacher_groups[l["teacher"]].append(i)
        subject_groups[(l["class_id"], l["subject"])].append(i)

    # Class and teacher no-double-booking.
    for groups in (class_groups, teacher_groups):
        for inds in groups.values():
            for d in range(nd):
                for p in range(np):
                    model.add_at_most_one(x[i, d, p] for i in inds)

    # Class occupancy helper for free-period restrictions.
    class_occupied = {}
    for class_id, inds in class_groups.items():
        for d in range(nd):
            for p in range(np):
                occ = model.new_bool_var(f"occ_cls_{class_id}_d{d}_p{p}")
                model.add_max_equality(occ, [x[i, d, p] for i in inds])
                class_occupied[class_id, d, p] = occ

    # Subject maximum per day.
    for key, inds in subject_groups.items():
        limit = lessons[inds[0]]["max_sessions_per_day"]
        for d in range(nd):
            model.add(sum(x[i, d, p] for i in inds for p in range(np)) <= limit)

    # Availability.
    for item in rules.get("teacher_unavailable", []):
        d = day_index(days, item["day"])
        p = period_index(periods, item["period"])
        for i, l in enumerate(lessons):
            if l["teacher"] == item["teacher"]:
                model.add(x[i, d, p] == 0)

    for item in rules.get("class_unavailable", []):
        d = day_index(days, item["day"])
        p = period_index(periods, item["period"])
        for i, l in enumerate(lessons):
            if l["class_id"] == item["class"]:
                model.add(x[i, d, p] == 0)

    # Free-period restrictions: enforce that slots not allowed to be free are occupied.
    for restriction in rules.get("free_period_restrictions", []):
        class_id = restriction["class"]
        allowed_days = {day_index(days, day) for day in restriction.get("allowed_days", [])}
        allowed_positions = set()
        for position in restriction.get("allowed_period_positions", []):
            pos_norm = str(position).strip().lower()
            if pos_norm in {"first", "last"}:
                allowed_positions.update(period_positions(period_groups, pos_norm))
            elif pos_norm.isdigit():
                allowed_positions.add(period_index(periods, int(pos_norm)))
            else:
                allowed_positions.add(period_index(periods, position))
        for d in range(nd):
            for p in range(np):
                if d not in allowed_days and p not in allowed_positions:
                    model.add(class_occupied[class_id, d, p] == 1)

    # Fixed lessons. Match session number when supplied; otherwise use first unassigned-looking match.
    for fixed in rules.get("fixed_lessons", []):
        matches = [i for i, l in enumerate(lessons)
                   if l["class_id"] == fixed["class"] and l["subject"] == fixed["subject"] and l["teacher"] == fixed["teacher"]]
        if not matches:
            raise ValueError(f"Fixed lesson not found: {fixed['class']} / {fixed['subject']} / {fixed['teacher']}")
        if "session" in fixed:
            matches = [i for i in matches if lessons[i]["session_number"] == int(fixed["session"])]
            if not matches:
                raise ValueError(f"Fixed session not found: {fixed}")
        d = day_index(days, fixed["day"])
        p = period_index(periods, fixed["period"])
        if len(matches) == 1:
            model.add(x[matches[0], d, p] == 1)
        else:
            model.add(sum(x[i, d, p] for i in matches) == 1)

    # Consecutive class/teacher hard limits.
    for groups, rule_name in ((class_groups, "max_class_consecutive_periods"),
                              (teacher_groups, "max_teacher_consecutive_periods")):
        limit = rules.get(rule_name)
        if limit:
            limit = int(limit)
            w = limit + 1
            if w <= np:
                for inds in groups.values():
                    for d in range(nd):
                            for group in period_groups:
                                if len(group) >= w:
                                    for start in range(len(group) - w + 1):
                                        model.add(sum(x[i, d, p] for i in inds for p in group[start:start + w]) <= limit)
    if include_objective:
        objective = []
        # ----------------------------------------------------
        # PRIORITY A: pack subject sessions onto as few days as
        # allowed by max_sessions_per_day.
        # Convex pair reward: C(k,2) rewards 3+2 more than 1+1+1+1+1.
        # ----------------------------------------------------
        for (class_id, subject), inds in subject_groups.items():
            for d in range(nd):
                for a in range(len(inds)):
                    for b in range(a + 1, len(inds)):
                        both = model.new_bool_var(f"pair_{class_id}_{a}_{b}_d{d}")
                        # both = 1 iff lesson a and b are on this day.
                        day_a = sum(x[inds[a], d, p] for p in range(np))
                        day_b = sum(x[inds[b], d, p] for p in range(np))
                        model.add(both <= day_a)
                        model.add(both <= day_b)
                        model.add(both >= day_a + day_b - 1)
                        objective.append((1000, both))

        for pref in rules.get("subject_position_preferences", []):
            class_id = pref["class"]
            subject = pref["subject"]
            when = str(pref["when"]).strip().lower()
            weight = int(pref.get("weight", 50))
            pref_inds = subject_groups.get((class_id, subject), [])
            if not pref_inds:
                continue
            positions = period_positions(period_groups, when)
            for i in pref_inds:
                for d in range(nd):
                    for p in positions:
                        objective.append((weight, x[i, d, p]))

        # ----------------------------------------------------
        # PRIORITY B: keep all sessions of a class-subject on a
        # day in one continuous block. The hard no-gap rule is
        # below; this reward prefers compact blocks.
        # ----------------------------------------------------
        for (class_id, subject), inds in subject_groups.items():
            for d in range(nd):
                occupied = []
                for p in range(np):
                    y = model.new_bool_var(f"occ_{class_id}_{subject}_d{d}_p{p}")
                    model.add_max_equality(y, [x[i, d, p] for i in inds])
                    occupied.append(y)
                # No 1-0-1 pattern: subject sessions on a day must be contiguous within teaching blocks.
                for group in period_groups:
                    for index in range(1, len(group) - 1):
                        p = group[index]
                        model.add(occupied[group[index - 1]] + occupied[group[index + 1]] - 1 <= occupied[p])
                    # Small bonus for adjacent occupied slots within each contiguous block.
                    for index in range(len(group) - 1):
                        p = group[index]
                        q = group[index + 1]
                        adjacent = model.new_bool_var(f"adj_{class_id}_{subject}_d{d}_p{p}_{q}")
                        model.add(adjacent <= occupied[p])
                        model.add(adjacent <= occupied[q])
                        model.add(adjacent >= occupied[p] + occupied[q] - 1)
                        objective.append((100, adjacent))

        # ----------------------------------------------------
        # PRIORITY C: avoid splitting a teacher's same subject
        # work unnecessarily, while never overriding hard rules.
        # ----------------------------------------------------
        teacher_subject_groups = defaultdict(list)
        for i, l in enumerate(lessons):
            teacher_subject_groups[(l["teacher"], l["class_id"], l["subject"])].append(i)
        for key, inds in teacher_subject_groups.items():
            if len(inds) < 2:
                continue
            for d in range(nd):
                for a in range(len(inds)):
                    for b in range(a + 1, len(inds)):
                        both = model.new_bool_var(f"ts_pair_{abs(hash(key))}_{a}_{b}_{d}")
                        da = sum(x[inds[a], d, p] for p in range(np))
                        db = sum(x[inds[b], d, p] for p in range(np))
                        model.add(both <= da)
                        model.add(both <= db)
                        model.add(both >= da + db - 1)
                        objective.append((20, both))

        # ----------------------------------------------------
        # PRIORITY D: prefer reasonable distribution only after
        # packing. A day-use reward is intentionally much weaker.
        # ----------------------------------------------------
        if rules.get("spread_subject_across_days", True):
            for (class_id, subject), inds in subject_groups.items():
                for d in range(nd):
                    used = model.new_bool_var(f"used_{class_id}_{subject}_d{d}")
                    model.add_max_equality(used, [x[i, d, p] for i in inds for p in range(np)])
                    objective.append((1, used))

        # Avoid unnecessary same-subject adjacency is intentionally NOT used.
        # Consecutive same-subject lessons are now preferred.

        model.maximize(sum(weight * var for weight, var in objective))

    return model, x, class_groups, teacher_groups, subject_groups


def test_feasibility(data, lessons):
    print("\nRunning hard-constraint feasibility test...")
    model, _, _, _, _ = make_model(data, lessons, include_objective=False)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30
    solver.parameters.num_workers = 8
    status = solver.solve(model)
    return status, solver


def generate_timetable(data):
    lessons = build_lessons(data)
    print(f"Total lessons to schedule: {len(lessons)}")

    direct_issues = diagnostic_report(data, lessons)
    if direct_issues:
        print("\nERROR: Timetable cannot be generated until the issues above are fixed.")
        return None

    # First test feasibility WITHOUT any optimization. This prevents an
    # objective from hiding a real hard-constraint problem.
    hard_status, hard_solver = test_feasibility(data, lessons)
    if hard_status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        print("\nERROR: Hard constraints are infeasible.")
        print("The problem is not the optimization objective.")
        print("Check the diagnostic information above, especially teacher/class availability and fixed lessons.")
        print("CP-SAT status:", hard_solver.status_name(hard_status))
        return None

    print("OK: Hard constraints are feasible.")
    print("\nStarting CP-SAT optimization...\n")

    model, x, _, _, _ = make_model(data, lessons, include_objective=True)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 120
    solver.parameters.num_workers = 8
    solver.parameters.log_search_progress = False
    solver.parameters.random_seed = 42

    status = solver.solve(model)
    if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        print("\nERROR: Optimization failed to return a timetable.")
        print("Status:", solver.status_name(status))
        return None

    print("\nOK: Timetable generated.")
    print("Solver status:", solver.status_name(status))
    print("Objective:", solver.objective_value)
    print("Time:", solver.wall_time, "seconds")

    school = data["school"]
    days = school["days"]
    periods = school["periods"]
    timetable = []
    for i, lesson in enumerate(lessons):
        for d in range(len(days)):
            for p in range(len(periods)):
                if solver.value(x[i, d, p]):
                    timetable.append({
                        "lesson_id": lesson["id"],
                        "class_id": lesson["class_id"],
                        "class": lesson["class_name"],
                        "subject": lesson["subject"],
                        "teacher": lesson["teacher"],
                        "session": lesson["session_number"],
                        "day": days[d],
                        "day_index": d,
                        "period": periods[p]["number"],
                        "period_name": periods[p]["name"],
                        "start": periods[p].get("start"),
                        "end": periods[p].get("end"),
                    })
                    break
            else:
                continue
            break

    return {
        "school": school["name"],
        "days": days,
        "periods": periods,
        "timetable": timetable,
        "solver": {
            "status": solver.status_name(status),
            "objective": solver.objective_value,
            "wall_time": solver.wall_time,
            "total_lessons": len(lessons),
        },
    }


def save_json(result):
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"JSON saved to {OUTPUT_JSON}")


def save_csv(result):
    fields = ["class", "day", "period", "period_name", "start", "end", "subject", "teacher", "session"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in result["timetable"]:
            writer.writerow({k: row.get(k) for k in fields})
    print(f"CSV saved to {OUTPUT_CSV}")


def display_class_timetables(result):
    days = result["days"]
    periods = result["periods"]
    rows = result["timetable"]
    classes = sorted({r["class"] for r in rows})
    lookup = {(r["class"], r["day_index"], r["period"]): r for r in rows}

    for cls in classes:
        print("\n" + "=" * 100)
        print(f"CLASS: {cls}")
        print("=" * 100)
        header = ["Period"] + days
        print(" | ".join(f"{h:<22}" for h in header))
        print("-" * (25 * len(header)))
        for period in periods:
            period_number = period["number"]
            cells = [f"P{period_number} {period['name']}"]
            for d, day in enumerate(days):
                r = lookup.get((cls, d, period_number))
                cells.append(f"{r['subject']} ({r['teacher']})" if r else "FREE")
            print(" | ".join(f"{c:<22}" for c in cells))


def main():
    try:
        data = load_data()
        validate_data(data)
        result = generate_timetable(data)
        if result is None:
            return
        save_json(result)
        save_csv(result)
        display_class_timetables(result)
    except Exception as e:
        print("\nERROR:", e)


if __name__ == "__main__":
    main()