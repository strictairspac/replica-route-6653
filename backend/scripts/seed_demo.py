from sqlalchemy import select,func
from src.config.database import Session,Base,engine
from src.models.entities import User,Node,RequestSet,Trial,MigrationPlan
from src.utils.security import password_hash

def migration_seeds():
    # A：全链路综合演练——存量拷贝/增量复制/持续写入并发交错，目标故障、拒绝切流、
    # 重试幂等、旧包不覆盖新写、切流后迟到写的转发与拒绝。
    plan_a={
        'code':'MG-1117-A','title':'键范围在线搬迁全链路（user:100–user:199）',
        'key_start':'user:100','key_end':'user:199',
        'source_shard':'shard-harbor','target_shard':'shard-bay',
        'source_initial':[{'key':'user:100','value':'snap-100'},{'key':'user:120','value':'snap-120'},
                          {'key':'user:150','value':'snap-150'},{'key':'user:190','value':'snap-190'},
                          {'key':'user:200','value':'other-range-keep'}],
        'target_initial':[],
        'events':[
            {'at_ms':0,'kind':'write','key':'user:100','value':'write-100-v2'},
            {'at_ms':0,'kind':'copy_snapshot','key':'user:150','value':'snap-150','version':1},
            {'at_ms':50,'kind':'copy_snapshot','key':'user:100','value':'snap-100','version':1},
            {'at_ms':100,'kind':'write','key':'user:120','value':'write-120-v2'},
            {'at_ms':150,'kind':'stream','key':'user:120','value':'write-120-v2','version':2},
            {'at_ms':200,'kind':'stream','key':'user:100','value':'write-100-v2','version':2},
            {'at_ms':250,'kind':'write','key':'user:100','value':'write-100-v3'},
            {'at_ms':280,'kind':'copy_snapshot','key':'user:300','value':'out-of-range','version':1},
            {'at_ms':300,'kind':'target_down'},
            {'at_ms':350,'kind':'stream','key':'user:100','value':'write-100-v3','version':3},
            {'at_ms':400,'kind':'cutover'},
            {'at_ms':450,'kind':'target_up'},
            {'at_ms':500,'kind':'stream','key':'user:100','value':'write-100-v3','version':3},
            {'at_ms':540,'kind':'stream','key':'user:100','value':'write-100-v3','version':3},
            {'at_ms':560,'kind':'copy_snapshot','key':'user:100','value':'snap-100','version':1},
            {'at_ms':580,'kind':'copy_snapshot','key':'user:190','value':'snap-190','version':1},
            {'at_ms':600,'kind':'write','key':'user:150','value':'write-150-v2'},
            {'at_ms':650,'kind':'stream','key':'user:150','value':'write-150-v2','version':2},
            {'at_ms':700,'kind':'cutover'},
            {'at_ms':750,'kind':'write','key':'user:120','value':'late-via-old-route','route_hint':'old'},
            {'at_ms':800,'kind':'write','key':'user:190','value':'new-route-write'},
            {'at_ms':820,'kind':'read','key':'user:120'},
            {'at_ms':830,'kind':'read','key':'user:200'},
            {'at_ms':850,'kind':'source_down'},
            {'at_ms':900,'kind':'write','key':'user:120','value':'late-orphan','route_hint':'old'},
        ],
        'note':'并发交错：拷贝/复制/写同窗口推进；含目标故障、切流闸门、迟到写处置'}
    # B：目标始终未补齐，切流必须被拒绝，归属留在源分片。
    plan_b={
        'code':'MG-1117-B','title':'未补齐拒绝切流（user:10–user:19）',
        'key_start':'user:10','key_end':'user:19',
        'source_shard':'shard-harbor','target_shard':'shard-bay',
        'source_initial':[{'key':'user:10','value':'k10'},{'key':'user:11','value':'k11'}],
        'target_initial':[],
        'events':[
            {'at_ms':0,'kind':'copy_snapshot','key':'user:10','value':'k10','version':1},
            {'at_ms':100,'kind':'write','key':'user:11','value':'k11-v2'},
            {'at_ms':200,'kind':'cutover'},
            {'at_ms':300,'kind':'read','key':'user:11'},
        ],
        'note':'目标缺少 user:11，切流闸门拒绝；读仍走源分片'}
    # C：目标故障窗口后重试，重复增量包只应用一次。
    plan_c={
        'code':'MG-1117-C','title':'故障恢复重试幂等（order:0–order:9）',
        'key_start':'order:0','key_end':'order:9',
        'source_shard':'shard-harbor','target_shard':'shard-bay',
        'source_initial':[{'key':'order:1','value':'o1'}],
        'target_initial':[],
        'events':[
            {'at_ms':0,'kind':'stream','key':'order:1','value':'o1','version':1},
            {'at_ms':100,'kind':'write','key':'order:2','value':'o2-v1'},
            {'at_ms':150,'kind':'target_down'},
            {'at_ms':200,'kind':'stream','key':'order:2','value':'o2-v1','version':1},
            {'at_ms':250,'kind':'target_up'},
            {'at_ms':300,'kind':'stream','key':'order:2','value':'o2-v1','version':1},
            {'at_ms':350,'kind':'stream','key':'order:2','value':'o2-v1','version':1},
            {'at_ms':400,'kind':'cutover'},
            {'at_ms':450,'kind':'read','key':'order:2'},
        ],
        'note':'失败的复制在目标恢复后重试，重复投递版本闸幂等跳过'}
    return [plan_a,plan_b,plan_c]

def seed():
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ('admin','operator','viewer'):
            email=role+'@qiyuan.test'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,password=password_hash('Demo2026!'),role=role))
        for name,role,pos,delay,note in [('harbor-primary','primary',105,0,'栖原主站 · 写入航道'),('north-replica','replica',104,150,'北站副本 · 延迟偏高'),('south-replica','replica',105,20,'南站副本 · 常用读航道')]:
            if not db.scalar(select(Node).where(Node.name==name)):db.add(Node(name=name,role=role,status='healthy',lineage='qiyuan-log-01',position=pos,delay_ms=delay,note=note))
        mixed=[{'at_ms':0,'kind':'write','session':'lab-A'},{'at_ms':100,'kind':'read','session':'lab-A'},{'at_ms':600,'kind':'replicate','node':'south-replica','position':106,'delay_ms':20},{'at_ms':700,'kind':'read','session':'lab-A'},{'at_ms':800,'kind':'node_down','node':'south-replica'},{'at_ms':900,'kind':'read','session':'lab-A'},{'at_ms':1000,'kind':'node_down','node':'harbor-primary'},{'at_ms':1100,'kind':'read','session':'lab-A'}]
        cases=[('QY-1117-A','交接班写后读与双节点故障',mixed),('QY-1117-B','延迟超标回退主库',[{'at_ms':0,'kind':'node_down','node':'south-replica'},{'at_ms':10,'kind':'read','session':'visitor'}]),('QY-1117-C','双副本不可读时拒绝',[{'at_ms':0,'kind':'write','session':'lab-B'},{'at_ms':1000,'kind':'node_down','node':'harbor-primary'},{'at_ms':1100,'kind':'read','session':'lab-B'}])]
        for code,title,events in cases:
            if not db.scalar(select(RequestSet).where(RequestSet.code==code)):db.add(RequestSet(code=code,title=title,events=events,note='栖原站点值班交接 · 09:15 请求轨迹'))
        for payload in migration_seeds():
            if not db.scalar(select(MigrationPlan).where(MigrationPlan.code==payload['code'])):
                db.add(MigrationPlan(**payload))
        db.commit();print({cls.__tablename__:db.scalar(select(func.count()).select_from(cls)) for cls in (User,Node,RequestSet,Trial,MigrationPlan)})
if __name__=='__main__':seed()
