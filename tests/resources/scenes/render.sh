#!/bin/sh
# Renders every scene page here into a PNG of the same name, 1728 x 1117 at twice the pixels as a Retina screen
# records it (the two-tile crop 560 wide), with headless Chrome. Run it again after changing a page; the tests read
# the PNGs. The PNGs and the portraits are kept in git (the pre-commit hook lets images under tests/resources/
# through); the portraits are fetched from Wikimedia Commons when missing, public domain as
# `../public_domain_faces/SOURCES.md` says.
cd "$(dirname "$0")" || exit 1
for name in Neil_Armstrong_pose Buzz_Aldrin Sally_Ride_1984; do
  file="../public_domain_faces/$name.jpg"
  [ -f "$file" ] && continue
  page=$( [ "$name" = Sally_Ride_1984 ] && echo "Sally_Ride_(1984).jpg" || echo "$name.jpg" )
  curl -sfL -o "$file" "https://commons.wikimedia.org/wiki/Special:FilePath/$page?width=480" || { echo "could not fetch $page"; exit 1; }
done
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
for page in *.html; do
  case "$page" in call-two-tiles.html) size=560,1117 ;; *) size=1728,1117 ;; esac
  "$CHROME" --headless=new --hide-scrollbars --allow-file-access-from-files --window-size="$size" --force-device-scale-factor=2 \
    --screenshot="$PWD/${page%.html}.png" "file://$PWD/$page" > /dev/null 2>&1
done
# The capture loop's video: a call on the web whose camera grid shows faces in both columns or in the outer column
# only, eight seconds each, the share's list changing once, so frames are taken while the faces seen change
# then one face only, the list changing while it lasts (a run lost the cut when the detector saw one camera)
printf "file '%s'\nduration 8\n" loop-grid-a-both.png loop-grid-a-outer.png loop-grid-b-outer.png loop-grid-b-both.png \
  loop-grid-a-outer.png loop-grid-a-one.png loop-grid-b-one.png > loop-grid.txt
printf "file 'loop-grid-b-one.png'\n" >> loop-grid.txt
ffmpeg -y -loglevel error -f concat -i loop-grid.txt -r 5 -c:v libx264 -crf 18 -pix_fmt yuv420p loop-grid.mp4
rm loop-grid.txt
# The broadcast loop's video: a slide beside the speaker's camera, then another slide whose screen box starts a pixel in
# from a black left edge, then the first again (a run kept the camera on every frame taken while the box started there)
printf "file '%s'\nduration 8\n" broadcast-slide-camera.png broadcast-slide-edge-camera.png broadcast-slide-camera.png > loop-broadcast.txt
printf "file 'broadcast-slide-camera.png'\n" >> loop-broadcast.txt
ffmpeg -y -loglevel error -f concat -i loop-broadcast.txt -r 5 -c:v libx264 -crf 18 -pix_fmt yuv420p loop-broadcast.mp4
rm loop-broadcast.txt
# The framed share's loop: a call on the web whose share fills the stage inside a dark outline, the presenter's name
# label across its bottom-left corner, the call's timer and the shared list changing; then a slide whose large bordered
# table changes with its title, which must not be cut to the table (a run kept the whole call window on every frame)
# The light-theme copy: the outline 221 grey between a 232 rail and a 243 share, as a call on the web in a light theme
# draws it (DEFECTS 99: a 40-level edge rule missed it and the whole browser window was kept)
# The stage copy: the shared screen letterboxed on the call's flat grey stage, the name label in the band below it
# (DEFECTS 109: the cut stopped at the stage's line and kept both bands)
for loop in loop-web-share loop-web-share-light loop-web-share-stage loop-slide-table; do
  printf "file '%s'\nduration 8\n" $loop-a.png $loop-b.png $loop-a.png $loop-b.png > $loop.txt
  printf "file '%s'\n" $loop-b.png >> $loop.txt
  ffmpeg -y -loglevel error -f concat -i $loop.txt -r 5 -c:v libx264 -crf 18 -pix_fmt yuv420p $loop.mp4
  rm $loop.txt
done
# The lecture loop: a room camera's blackboard with the lecturer standing at three places, eight seconds each, then
# back at the first, then the board gaining two lines with the lecturer stepped aside (DEFECTS 100: each move took a frame, the board
# unchanged)
printf "file '%s'\nduration 8\n" loop-lecture-walk-a.png loop-lecture-walk-b.png loop-lecture-walk-c.png \
  loop-lecture-walk-a.png loop-lecture-walk-d.png > loop-lecture-walk.txt
printf "file 'loop-lecture-walk-d.png'\n" >> loop-lecture-walk.txt
ffmpeg -y -loglevel error -f concat -i loop-lecture-walk.txt -r 5 -c:v libx264 -crf 18 -pix_fmt yuv420p loop-lecture-walk.mp4
rm loop-lecture-walk.txt
# The reveal loop: the lecturer at the right, then at the left as new lines go up in the middle, then at the right
# again with a small table written where he stood, then back (DEFECTS 113: the table was left out with his move)
printf "file '%s'\nduration 8\n" loop-lecture-reveal-a.png loop-lecture-reveal-b.png loop-lecture-reveal-c.png \
  loop-lecture-reveal-d.png > loop-lecture-reveal.txt
printf "file 'loop-lecture-reveal-d.png'\n" >> loop-lecture-reveal.txt
ffmpeg -y -loglevel error -f concat -i loop-lecture-reveal.txt -r 5 -c:v libx264 -crf 18 -pix_fmt yuv420p loop-lecture-reveal.mp4
rm loop-lecture-reveal.txt
# The held slide's video: one slide still for 420 s, then another, at one frame a second (a slide held so long is taken
# again every 3 minutes, DEFECTS 157); its two pictures are kept in git
printf "file 'held-slide.png'\nduration 420\nfile 'held-slide-next.png'\nduration 30\nfile 'held-slide-next.png'\n" > held.txt
ffmpeg -y -loglevel error -f concat -i held.txt -r 1 -c:v libx264 -crf 23 -pix_fmt yuv420p held-slide-450s.mp4
rm held.txt
