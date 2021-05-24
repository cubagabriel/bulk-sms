###### SMPP Bulk SMS

Platform to send million of SMS in a scalable way, given that you have direct SMPP connection to some SMSC.

It uses:

- full python code
- smpplib library for connection to SMSC
- python sched library for fixed rate SMS submiting in order to avoid throttling 
- ZMQ for communication between the front-end and the N-workers
- python bottle library for REST API
- NGINX as the HTTP server and uWSGI
- Docker and docker-compose for deployment

It features:

- Up to 1000 TPS per worker as tested
- Stateless and ephemeral components
- Unlimited number of destination per loads
- Simultaneous SMS loads
- Automatic handling of encoding and SMS fragmentation
- Multiple load balancers for different kinds of loads and rates

Tested with:

- Mavenir SMSC
- Melroselabs open source SMSC, which you can build and test here https://github.com/melroselabs/smpp-smsc-simulator

Installation:

1. Clone the repo
2. Configure global_config.ini
3. Configure docker-compose
4. Build the containers 
5. Execute docker-compose up -d

Usage:

Just execute the client with the HTTP Server IP and follow the menu (currently in spanish). The input file format can be found in test_data directory

You can also use the REST API with tools like cURL.