# -*- coding: utf-8 -*-
import os
import glob
import csv
import pandas as pd

# フォルダ中のパスを取得
DATA_PATH = "./csv/"
All_Files = glob.glob('{}*.csv'.format(DATA_PATH))

# フォルダ中の全csvをマージ
list = []
for file in All_Files:
    list.append(pd.read_csv(file))

df = pd.concat(list, sort=False)
df.to_csv('rurubu.csv', encoding='utf_8')
