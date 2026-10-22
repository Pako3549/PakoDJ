# PakoDJ production reverse proxy image (target of the deploy composes).
# The whole /nginx/prod.conf is baked in so deploys never depend on the
# runner workspace state (a bad bind mount once left a stray directory on
# the Pi and broke the stack — baking it removes that failure class).
FROM nginx:stable-alpine
COPY prod.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
