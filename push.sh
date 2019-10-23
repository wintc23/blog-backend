git add .
git commit -m publish
git push
ssh root@wintc.top << autoscript
cd ~
source env.sh
cd /home/lushg/blog-backend
source venv/bin/activate
git pull origin master
pip install -r requirement.txt
lsof -i:5000 | grep 5000 | awk '{ print \$2 }' | xargs kill -9
nohup python main.py runserver --host 0.0.0.0 &
exit
autoscript
echo 'done'