# Steady nozzle pressure per flow step from a flow_test.py capture, compared
# with the straight-line trend of 6-12 mm3/s. The hotend's melt limit shows
# as pressure rising above the trend (PETG 250C: +13% at 18 mm3/s); extruder
# slip as pressure that stops rising (PLA 220C: from about 26-28 mm3/s).
# This file may be distributed under the terms of the GNU GPLv3 license.
import csv, statistics, sys
name = sys.argv[1]
P=[(float(r['print_time']),float(r['raw'])) for r in csv.DictReader(open(name+'_pressure.csv'))]
E=[(float(r['print_time']),float(r['move_t']),float(r['start_v']),float(r['accel'])) for r in csv.DictReader(open(name+'_extruder.csv'))]
cru=[(t,mt,v) for t,mt,v,a in E if a==0 and mt>0.5]
rows=[]
for i,(t,mt,v) in enumerate(cru):
    steady=[x for tt,x in P if t+mt-1.5 < tt < t+mt-0.05]
    nxt=cru[i+1][0] if i+1<len(cru) else t+mt+0.5
    rest=[x for tt,x in P if nxt-0.20 < tt < nxt-0.02]
    if steady and rest: rows.append((round(v*2.405), statistics.median(steady), statistics.median(rest)))
# Extruder servo samples (MOTOR_LOG_START in flow_test.py): median iq
# current and mean encoder speed in the same steady window of each step
motor={}
try:
    M=[(float(r['print_time']),float(r['speed'] or 'nan'),float(r['iq'] or 'nan')) for r in csv.DictReader(open(name+'_motor.csv'))]
    for i,(t,mt,v) in enumerate(cru):
        win=[(s,q) for tt,s,q in M if t+mt-1.5 < tt < t+mt-0.05 and q==q]
        if win: motor[round(v*2.405)]=(statistics.median(q for s,q in win), statistics.mean(s for s,q in win), len(win))
except FileNotFoundError:
    pass
rows=[r for r in rows if r[0]>=6]
base=statistics.median([r[2] for r in rows[:2]])
pts=[(f, abs(s-base), abs(r-base)) for f,s,r in rows]
low=[p for p in pts if p[0]<=12]
n=len(low); mx=sum(p[0] for p in low)/n; my=sum(p[1] for p in low)/n
b=sum((p[0]-mx)*(p[1]-my) for p in low)/sum((p[0]-mx)**2 for p in low); a=my-b*mx
iqlow=[(f,motor[f][0]) for f,_,_ in pts if f in motor and f<=12]
if len(iqlow)>=2:
    n2=len(iqlow); mx2=sum(f for f,q in iqlow)/n2; my2=sum(q for f,q in iqlow)/n2
    b2=sum((f-mx2)*(q-my2) for f,q in iqlow)/sum((f-mx2)**2 for f,q in iqlow); a2=my2-b2*mx2
print("flow  force   vs 6-12 trend   leftover after 150 ms" + ("   motor iq  vs trend  speed rad/s (n)" if motor else ""))
knee=None
for f,force,rest in pts:
    ex=100*(force-(a+b*f))/(a+b*f)
    line="%4d  %7.0f   %+5.1f%%         %6.0f (%4.1f%% of force)" % (f, force, ex, rest, 100*rest/force)
    if f in motor:
        iq,spd,cnt=motor[f]
        exq=100*(iq-(a2+b2*f))/(a2+b2*f) if len(iqlow)>=2 and (a2+b2*f) else float('nan')
        line+="   %7.3f A %+5.1f%%   %6.2f (%d)" % (iq, exq, spd, cnt)
    print(line)
    if knee is None and ex > 10: knee=f
print("first flow more than 10%% above the low-flow trend: %s mm3/s" % knee)
