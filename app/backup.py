# 备份到github，需配置仓库地址

import os
from flask import current_app
from threading import Thread

def git_backup_async (git_dir, path, sText):
  path_list = path.split('/')
  length = len(path_list)
  if length > 1:
    path_list = path_list[0:length - 1]
    dir_path = git_dir + '/'.join(path_list)
    if not os.path.exists(dir_path):
      os.makedirs(dir_path)
  f = open(git_dir + path, 'w+')
  f.write(sText)
  print('写入内容完毕！')
  f.close()
  os.system('''
    cd %s
    git add .
    git commit -m backup
    git push -u origin master
    echo 备份完毕
  '''%git_dir)


def git_backup (path, sText):
  app = current_app._get_current_object()
  git_dir = app.config.get('GIT_BACKUP_DIR')
  if not git_dir:
    print("\033[31m未配置备份仓库地址\033[0m")
    return  
  if not os.path.exists(git_dir):
    print("\033[31m备份仓库地址不存在\033[0m")
    return
  thr = Thread(target=git_backup_async, args=[git_dir, path, sText])
  thr.start()
  return thr
