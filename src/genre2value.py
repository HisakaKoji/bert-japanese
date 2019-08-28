import pandas as pd
import os

table = {
'節句・年中行事'             :  'Traditional-Festivalsand-annual-events'   ,
'神輿・山車など'             :  'Shrine-floats-etc.'   ,
'伝統芸能・舞踊'             :  'Traditional-performing-arts-and-dance'   ,
'行列・パレード'             :  'Procession-and-parade'   ,
'食の祭り'                     :  'food'   ,
'市・縁日'                   :  'market'   ,
'花見・自然'                 :  'flower-nature'   ,
'火と灯の祭り'               :  'fire'   ,
'花火大会'                   :  'fireworks'   ,
'雪・冬祭り'                 :  'snow'  ,
'イルミネーション'           :  'illumination'  ,
'音楽祭・映画祭'                 :  'music'  ,
'スポーツ'                   :  'sports'  ,
'美術展・博物展'             :  'museum'  ,
'博物館・美術館展'           :  'museum'  ,
'フェスティバル'     :  'festival'  ,
'動物の祭り'               :  'animal'  ,
'体験イベント'               :  'experience'  ,
'学園祭'                     :  'school'  ,
'講演会・トークショー'       :  'talk'  ,
'演劇・舞台'                 :  'stage'  ,
'テーマパーク'               :  'thema-park'  ,
'動物園・水族館'             :  'animal-fish-park'  ,
'記念日'                     :  'anniversary'  ,
'即売会・フェア'             :  'fair'  ,
'その他'                     :  'other'  ,
'産業・商工祭'               :  'Industry'  ,
'習俗・風俗'                 :  'Customs'  ,
'農耕儀礼'                   : 'Agricultural ritual' ,
'0'                          : 'none'
}

df = pd.read_csv('./20190823.csv',delimiter=',')
events = df[df['kind'] == 'イベント']
genres = events['genre'].str.split('／',expand=True)
events['genre'] = genres[0]
events['genre2'] = genres[1]
events['genre2_num'] = events['genre2'].replace(table)
events['genre_num'] = events['genre'].replace(table)
events.to_csv('./genre_test_changed.txt')

df = pd.DataFrame()
df['text'] = events['event_name']
df['label'] = events['genre_num']

EXTRACTDIR = "."
df[:len(df) // 5].to_csv( os.path.join(EXTRACTDIR, "test.tsv"), sep='\t', index=False)
df[len(df) // 5:len(df)*2 // 5].to_csv( os.path.join(EXTRACTDIR, "dev.tsv"), sep='\t', index=False)
df[len(df)*2 // 5:].to_csv( os.path.join(EXTRACTDIR, "train.tsv"), sep='\t', index=False)

