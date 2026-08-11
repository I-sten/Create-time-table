import json
from pathlib import Path
from ortools.sat.python import cp_model

with Path('school_data.json').open('r', encoding='utf-8') as f:
    data = json.load(f)

def normalize_days(days):
    return [d['name'] if isinstance(d, dict) else str(d) for d in days]

def normalize_periods(periods):
    out=[]
    for p in periods:
        if isinstance(p, dict):
            out.append({'number': int(p['number']), 'name': p.get('name', p['number'])})
        else:
            out.append({'number': int(p), 'name': str(p)})
    return out

days = normalize_days(data['school']['days'])
periods = normalize_periods(data['school']['periods'])
nd = len(days)
pn = len(periods)
lessons=[]
for cls in data['classes']:
    for info in cls['subjects']:
        for n in range(int(info['sessions_per_week'])):
            lessons.append({'class_id': cls['id'], 'subject': info['subject'], 'teacher': info['teacher'], 'max_sessions_per_day': int(info.get('max_sessions_per_day', info['sessions_per_week']))})

class_groups={}
teacher_groups={}
subject_groups={}
for i,l in enumerate(lessons):
    class_groups.setdefault(l['class_id'], []).append(i)
    teacher_groups.setdefault(l['teacher'], []).append(i)
    subject_groups.setdefault((l['class_id'], l['subject']), []).append(i)

print('lessons', len(lessons), 'days', nd, 'periods', pn)
print('class groups sizes', {k: len(v) for k,v in class_groups.items()})
print('teacher groups max size', max(len(v) for v in teacher_groups.values()))
print('subject groups max size', max(len(v) for v in subject_groups.values()))

model = cp_model.CpModel()
x = {(i,d,p): model.NewBoolVar(f'x_{i}_{d}_{p}') for i in range(len(lessons)) for d in range(nd) for p in range(pn)}

# exactly once
for i in range(len(lessons)):
    model.Add(sum(x[i,d,p] for d in range(nd) for p in range(pn)) == 1)
solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds=30
status = solver.Solve(model)
print('after exactly once', solver.StatusName(status))

# no double booking class
for inds in class_groups.values():
    for d in range(nd):
        for p in range(pn):
            model.Add(sum(x[i,d,p] for i in inds) <= 1)
solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds=30
status = solver.Solve(model)
print('after class no-double', solver.StatusName(status))

# no double booking teacher
for inds in teacher_groups.values():
    for d in range(nd):
        for p in range(pn):
            model.Add(sum(x[i,d,p] for i in inds) <= 1)
solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds=30
status = solver.Solve(model)
print('after teacher no-double', solver.StatusName(status))

# subject max per day
for key, inds in subject_groups.items():
    limit = lessons[inds[0]]['max_sessions_per_day']
    for d in range(nd):
        model.Add(sum(x[i,d,p] for i in inds for p in range(pn)) <= limit)
solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds=30
status = solver.Solve(model)
print('after subject max/day', solver.StatusName(status))

# subject contiguity
for key, inds in subject_groups.items():
    for d in range(nd):
        occupied = [model.NewBoolVar(f'occ_{key}_{d}_{p}') for p in range(pn)]
        for p in range(pn):
            model.AddMaxEquality(occupied[p], [x[i,d,p] for i in inds])
        for p in range(1,pn-1):
            model.Add(occupied[p-1] + occupied[p+1] - 1 <= occupied[p])
solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds=30
status = solver.Solve(model)
print('after contiguity', solver.StatusName(status))

# teacher consecutive limit
limit = data['rules'].get('max_teacher_consecutive_periods')
if limit is not None:
    limit = int(limit)
    w = limit + 1
    if w <= pn:
        for inds in teacher_groups.values():
            for d in range(nd):
                for start in range(pn - w + 1):
                    model.Add(sum(x[i,d,p] for i in inds for p in range(start, start + w)) <= limit)
    solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds=30
    status = solver.Solve(model)
    print('after teacher consecutive', solver.StatusName(status))

print('done')
