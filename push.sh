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
python main.py db upgrade
pm2 stop blog-server
pm2 delete blog-server
pm2 start python --name blog-server -- main.py runserver --host 0.0.0.0
autoscript
echo 'done'