source venv/bin/activate
pip freeze > requirement.txt 
git add .
git commit -m publish
git pull origin master
git push
ssh root@8.129.22.92 << autoscript
cd ~
source env.sh
cd /home/lushg/blog-backend
source venv/bin/activate
git clean -df
git pull origin master
pip install --upgrade pip command
pip install -r requirement.txt
service mysql restart
python main.py db upgrade
pm2 restart pm2.json
autoscript
echo 'done'
