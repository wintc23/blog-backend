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
python main.py db upgrade
pm2 stop blog-server
pm2 delete blog-server
pm2 start python --name blog-server -- main.py runserver --host 0.0.0.0 --threaded
autoscript
echo 'done'
